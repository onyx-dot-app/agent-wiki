"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { Button, EndOfList, Tag, Text } from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import { SvgExternalLink, SvgFileText, SvgGlobe } from "@onyx-ai/opal/icons";
import {
  SvgConfluence,
  SvgGithub,
  SvgGmail,
  SvgGoogleDrive,
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

import { apiFetch } from "@/lib/api";
import { relativeTime } from "@/lib/time";
import type { SourceRef, SourceSpan, WriteProvenance } from "@/types";

import { PanelSearchField } from "./PanelSearch";

interface Props {
  path: string;
  headSha: string | null;
  /** Page body as served by /wiki/file. It can drift from HEAD (live
   * co-edit buffer, stale load), so span-offset snippets are best-effort. */
  body: string;
  sources: SourceRef[];
}

const SOURCE_ICONS: Record<string, IconFunctionComponent> = {
  confluence: SvgConfluence,
  github: SvgGithub,
  gmail: SvgGmail,
  google_drive: SvgGoogleDrive,
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

/** Identity used to join a source to its spans, mirroring the backend's
 * dedupe key (document id, falling back to url or title). */
function sourceKey(s: WriteProvenance): string {
  return s.source_document_id ?? s.source_url ?? s.source_title ?? "";
}

function SourceCard({
  source,
  snippet,
}: {
  source: SourceRef;
  snippet: string | undefined;
}) {
  const Icon = sourceIcon(source.source_type);
  const url = source.source_url;
  return (
    <div className="group/source w-full shrink-0 rounded-(--radius-12) p-1 hover:bg-(--background-tint-00)">
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
          <span className="invisible shrink-0 group-hover/source:visible">
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
        {snippet && (
          <span className="px-1">
            <Text font="secondary-body" color="text-03" maxLines={3}>
              {snippet}
            </Text>
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * Sources tab (mock 1837:103626): the ingested documents credited to this
 * page, each with the slice of page content its spans cover as a snippet.
 * Sources come with the page load, spans are fetched per head sha since
 * the offsets are remapped to HEAD server-side.
 */
export function SourcesPanel({ path, headSha, body, sources }: Props) {
  const [query, setQuery] = useState("");

  const { data: spans } = useSWR(
    headSha ? ["/wiki/source-spans", path, headSha] : null,
    () =>
      apiFetch<SourceSpan[]>(
        `/wiki/source-spans?path=${encodeURIComponent(path)}`,
      ),
  );

  // First live span per source, as the card's content preview. Spans arrive
  // ordered by offset, so the preview is the topmost credited slice.
  const snippets = useMemo(() => {
    const out = new Map<string, string>();
    for (const sp of spans ?? []) {
      const key = sourceKey(sp);
      if (!key || out.has(key)) continue;
      const text = body
        .slice(sp.start_offset, sp.end_offset)
        .replace(/\s+/g, " ")
        .trim();
      if (text) out.set(key, text.slice(0, 280));
    }
    return out;
  }, [spans, body]);

  const q = query.trim().toLowerCase();
  const shown = q
    ? sources.filter((s) =>
        [
          s.source_title,
          s.source_url,
          s.source_type,
          snippets.get(sourceKey(s)),
        ]
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

        {shown.map((s) => (
          <SourceCard
            key={sourceKey(s) || s.last_updated}
            source={s}
            snippet={snippets.get(sourceKey(s))}
          />
        ))}

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

"use client";

import { useState } from "react";
import {
  Button,
  Divider,
  InputTypeIn,
  Tag,
  Text,
} from "@onyx-ai/opal/components";
import { SvgEmpty, SvgNotFound } from "@onyx-ai/opal/illustrations";
import { SvgActivity, SvgX } from "@onyx-ai/opal/icons";
import { Content, IllustrationContent, Section } from "@onyx-ai/opal/layouts";
import { timeAgo } from "@onyx-ai/opal/time";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import {
  isNewActivity,
  toEventIso,
  useEvents,
  type AppEvent,
} from "@/lib/activities";
import { formatScopePath } from "@/lib/format";
import { useLeftPanel } from "@/providers/LeftPanelProvider";

interface TriggerFirePayload {
  doc_path?: string;
  change_kind?: string;
  reason?: string;
}

interface ActivityCardProps {
  event: AppEvent;
}

function ActivityCard({ event }: ActivityCardProps) {
  const p = event.payload as TriggerFirePayload;
  return (
    <div className="mx-3 my-1.5 rounded-(--border-radius-08) border border-(--border-01) bg-(--background-tint-00) px-3 py-2.5">
      <div className="mb-1 flex items-start justify-between gap-2">
        {p.doc_path ? (
          <span className="truncate font-mono text-xs text-(--text-04)">
            {formatScopePath(p.doc_path)}
          </span>
        ) : (
          <em className="text-xs text-(--text-02)">(no path)</em>
        )}
        {p.change_kind && (
          <span className="shrink-0 rounded-(--border-radius-04) bg-(--background-tint-03) px-1.5 py-[2px] text-[10px] font-semibold tracking-[0.3px] text-(--text-05) uppercase">
            {p.change_kind}
          </span>
        )}
      </div>
      {p.reason && (
        <p className="mb-1.5 line-clamp-2 text-xs text-(--text-04)">
          {p.reason}
        </p>
      )}
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-(--text-02)">
          trigger {event.target ?? "?"}
        </span>
        <span className="text-[11px] text-(--text-02)">
          {timeAgo(toEventIso(event.ts))}
        </span>
      </div>
    </div>
  );
}

export default function ActivitiesPanel() {
  const { toggleActivities } = useLeftPanel();
  const [query, setQuery] = useState("");
  const { events, isLoading } = useEvents(
    { kind: "trigger.fire", limit: 100 },
    { refreshInterval: 30_000 },
  );

  const unreadCount = events.filter((ev) => isNewActivity(ev.ts)).length;

  const filtered = query
    ? events.filter((ev) => {
        const p = ev.payload as TriggerFirePayload;
        const haystack = [p.doc_path, p.change_kind, p.reason, ev.target]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        return haystack.includes(query.toLowerCase());
      })
    : events;

  const newEvents = filtered.filter((ev) => isNewActivity(ev.ts));
  const olderEvents = filtered.filter((ev) => !isNewActivity(ev.ts));

  return (
    <div className="flex h-full w-(--activities-view) flex-col rounded-12 border border-border-01 p-1">
      {/* Header */}
      <Section
        flexDirection="row"
        alignItems="center"
        justifyContent="between"
        height="fit"
        padding={0.5}
      >
        <Content
          icon={SvgActivity}
          title="Activity History"
          variant="section"
          sizePreset="main-ui"
        />
        <Section
          flexDirection="row"
          justifyContent="end"
          gap={0.5}
          height="fit"
        >
          {unreadCount > 0 && (
            <Tag title={`${unreadCount} new`} color="blue" size="md" />
          )}
          <Button
            icon={SvgX}
            prominence="tertiary"
            size="lg"
            tooltip="Close"
            onClick={toggleActivities}
          />
        </Section>
      </Section>

      {/* Search — hidden when there are no events at all */}
      {!isLoading && events.length > 0 && (
        <div className="px-2 pb-2">
          <InputTypeIn
            searchIcon
            placeholder="Search activity history..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            clearButton
          />
        </div>
      )}

      {/* Body */}
      <div className="flex-1 overflow-y-auto py-1">
        {isLoading && <LoadingSpinner center />}

        {!isLoading && events.length === 0 && (
          <div className="flex h-full items-center justify-center">
            <IllustrationContent
              illustration={SvgEmpty}
              title="No activity yet."
            />
          </div>
        )}

        {!isLoading && events.length > 0 && filtered.length === 0 && (
          <div className="flex h-full items-center justify-center">
            <IllustrationContent
              illustration={SvgNotFound}
              title="No matching activity found."
            />
          </div>
        )}

        {newEvents.length > 0 && (
          <>
            <div className="px-3 pt-1">
              <Divider title="New" />
            </div>
            {newEvents.map((ev) => (
              <ActivityCard key={ev.id} event={ev} />
            ))}
          </>
        )}

        {olderEvents.length > 0 && (
          <>
            <div className="px-3 pt-1">
              <Divider title="Older" />
            </div>
            {olderEvents.map((ev) => (
              <ActivityCard key={ev.id} event={ev} />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

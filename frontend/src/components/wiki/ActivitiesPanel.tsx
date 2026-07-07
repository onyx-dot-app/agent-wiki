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
import {
  SvgActivity,
  SvgChevronDown,
  SvgChevronUp,
  SvgWorkflow,
  SvgX,
} from "@onyx-ai/opal/icons";
import { markdown } from "@onyx-ai/opal/utils";
import { Content, IllustrationContent, Section } from "@onyx-ai/opal/layouts";
import { timeAgo } from "@onyx-ai/opal/time";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { AvatarCluster, ScopeChip } from "@/components/triggers/fireParts";
import {
  isNewActivity,
  toEventIso,
  useEvents,
  type AppEvent,
} from "@/lib/activities";
import { useAuth } from "@/lib/auth";
import { useLeftPanel } from "@/providers/LeftPanelProvider";

interface ActivityPayload {
  doc_path?: string;
  change_kind?: string;
  reason?: string;
  message?: string;
  destination_type?: string;
  count?: number;
  threshold?: number;
  cap?: number;
}

// Searchable text for an event regardless of kind.
function eventHaystack(event: AppEvent): string {
  const p = event.payload as ActivityPayload;
  return [
    p.doc_path,
    p.change_kind,
    p.reason,
    p.message,
    event.target,
    event.kind,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function destinationName(type: string | undefined): string {
  if (type === "slack") return "Slack";
  if (type === "email") return "Email";
  return "Activity Center";
}

/** Collapsed one-liner (prefix + bold subject) + expandable body per kind. */
function eventTexts(event: AppEvent): {
  prefix: string;
  subject: string;
  body: string | null;
} {
  const p = event.payload as ActivityPayload;
  if (event.kind === "wiki.frequent_updates") {
    return {
      prefix: "Frequent auto-updates on",
      subject: p.doc_path ?? "a page",
      body: `Auto-updated ${p.count ?? "?"} times in the past 24 hours${
        p.threshold ? ` (warns at ${p.threshold})` : ""
      }.`,
    };
  }
  if (event.kind === "wiki.auto_update_capped") {
    return {
      prefix: "Auto-updates paused on",
      subject: p.doc_path ?? "a page",
      body: `Hit the limit of ${p.cap ?? "?"} auto-updates in 24 hours, so further auto-updates are paused for now.`,
    };
  }
  const verb =
    p.destination_type === "event_log" || !p.destination_type
      ? "Sent a notification to"
      : "Sent a message to";
  return {
    prefix: verb,
    subject: destinationName(p.destination_type),
    body: p.message || p.reason || null,
  };
}

function ActivityRow({
  event,
  ownerName,
}: {
  event: AppEvent;
  ownerName: string;
}) {
  const p = event.payload as ActivityPayload;
  const fresh = isNewActivity(event.ts);
  const { prefix, subject, body } = eventTexts(event);
  const [override, setOverride] = useState<boolean | null>(null);
  // New entries open expanded, older ones collapsed; the chevron overrides.
  const open = override ?? (fresh && body !== null);
  const Chevron = open ? SvgChevronUp : SvgChevronDown;

  const destinationTypes =
    event.kind === "trigger.fire" ? [p.destination_type ?? "event_log"] : [];

  return (
    <div className="flex w-full flex-col px-3 py-1">
      <div className="flex w-full items-center p-[2px]">
        <div className="flex min-w-0 flex-1 items-center gap-1 p-[2px]">
          <ScopeChip scope={p.doc_path ?? event.target ?? ""} />
          <span className="flex size-4 items-center justify-center p-[2px]">
            {event.kind === "trigger.fire" ? (
              <SvgWorkflow className="size-3 text-(--text-03)" />
            ) : (
              <SvgActivity className="size-3 text-(--text-03)" />
            )}
          </span>
          <AvatarCluster
            ownerName={ownerName}
            destinationTypes={destinationTypes}
          />
        </div>
        <div className="flex shrink-0 items-center gap-1 p-[2px]">
          <Text font="secondary-body" color="text-03" nowrap>
            {timeAgo(toEventIso(event.ts)) ?? ""}
          </Text>
          <span className="flex size-5 items-center justify-center">
            {fresh ? (
              <span className="size-[6px] rounded-full bg-(--action-link-05)" />
            ) : (
              <span className="size-2 rounded-full border border-(--border-02)" />
            )}
          </span>
        </div>
      </div>
      <div className="flex w-full flex-col p-1 pt-0">
        <div className="flex w-full items-center gap-[2px]">
          <div className="flex min-w-0 flex-1 items-center py-[2px]">
            <span className="shrink-0 px-[2px]">
              <Text font="secondary-body" color="text-03" nowrap>
                {prefix}
              </Text>
            </span>
            <span className="min-w-0 px-[2px]">
              <Text font="secondary-action" color="text-03" nowrap maxLines={1}>
                {subject}
              </Text>
            </span>
          </div>
          {body && (
            /* raw-ok: 20px inline chevron; Opal Button's smallest container oversizes this row */
            <button
              type="button"
              onClick={() => setOverride(!open)}
              aria-expanded={open}
              title="Details"
              className="flex size-5 shrink-0 cursor-pointer items-center justify-center rounded-(--radius-08) border-0 bg-transparent p-[2px] hover:bg-(--background-tint-02)"
            >
              <Chevron className="size-3.5 text-(--text-03)" />
            </button>
          )}
        </div>
        {open && body && (
          <div className="w-full pr-5 pb-1">
            <Text as="p" font="main-ui-body" color="text-03">
              {markdown(body)}
            </Text>
          </div>
        )}
      </div>
    </div>
  );
}

export default function ActivitiesPanel() {
  const { toggleActivities } = useLeftPanel();
  const { user } = useAuth();
  const ownerName = user?.name || user?.email || "?";
  const [query, setQuery] = useState("");
  const { events, isLoading } = useEvents(
    { limit: 100 },
    { refreshInterval: 30_000 },
  );

  const unreadCount = events.filter((ev) => isNewActivity(ev.ts)).length;

  const filtered = query
    ? events.filter((ev) => eventHaystack(ev).includes(query.toLowerCase()))
    : events;

  const newEvents = filtered.filter((ev) => isNewActivity(ev.ts));
  const olderEvents = filtered.filter((ev) => !isNewActivity(ev.ts));

  return (
    <div className="flex h-full w-(--activities-view) flex-col rounded-(--radius-12) border border-(--border-01) p-1">
      {/* Header */}
      <Section
        flexDirection="row"
        justifyContent="between"
        height="fit"
        gap={0.25}
      >
        <div className="p-2">
          <Content
            icon={SvgActivity}
            title="Activity History"
            variant="section"
            sizePreset="main-ui"
          />
        </div>
        <Section flexDirection="row" justifyContent="end" width="fit">
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
              <ActivityRow key={ev.id} event={ev} ownerName={ownerName} />
            ))}
          </>
        )}

        {olderEvents.length > 0 && (
          <>
            <div className="px-3 pt-1">
              <Divider title="Older" />
            </div>
            {olderEvents.map((ev) => (
              <ActivityRow key={ev.id} event={ev} ownerName={ownerName} />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

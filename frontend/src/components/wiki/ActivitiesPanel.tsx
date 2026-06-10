"use client";

import { Button, Divider } from "@onyx-ai/opal/components";
import { SvgX } from "@onyx-ai/opal/icons";
import { timeAgo } from "@onyx-ai/opal/time";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { useEvents, type AppEvent } from "@/lib/events";
import { formatScopePath } from "@/lib/format";
import { useLeftPanel } from "@/providers/LeftPanelProvider";

const NEW_CUTOFF_MS = 24 * 60 * 60 * 1000;

interface TriggerFirePayload {
  doc_path?: string;
  change_kind?: string;
  reason?: string;
}

// SQLite's datetime('now') omits the T and Z; treat as UTC.
function toIso(ts: string): string {
  return ts.includes("T") ? ts : `${ts.replace(" ", "T")}Z`;
}

function isNew(ts: string): boolean {
  return Date.now() - new Date(toIso(ts)).getTime() < NEW_CUTOFF_MS;
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
          <span className="shrink-0 rounded-(--border-radius-04) bg-(--background-tint-03) px-1.5 py-[2px] text-[10px] font-semibold uppercase tracking-[0.3px] text-(--text-05)">
            {p.change_kind}
          </span>
        )}
      </div>
      {p.reason && (
        <p className="mb-1.5 line-clamp-2 text-xs text-(--text-04)">{p.reason}</p>
      )}
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-(--text-02)">
          trigger {event.target ?? "?"}
        </span>
        <span className="text-[11px] text-(--text-02)">{timeAgo(toIso(event.ts))}</span>
      </div>
    </div>
  );
}

export function ActivitiesPanel() {
  const { toggleActivities } = useLeftPanel();
  const { events, isLoading } = useEvents(
    { kind: "trigger.fire", limit: 100 },
    { refreshInterval: 30_000 },
  );

  const newEvents = events.filter((ev) => isNew(ev.ts));
  const olderEvents = events.filter((ev) => !isNew(ev.ts));

  return (
    <div className="flex h-full flex-col rounded-(--border-radius-12) border border-(--border-01) bg-transparent">
      <div className="flex items-center justify-between border-b border-(--border-01) px-3 py-2">
        <span className="text-sm font-semibold text-(--text-05)">Activities</span>
        <Button
          icon={SvgX}
          prominence="tertiary"
          size="sm"
          tooltip="Close"
          onClick={toggleActivities}
        />
      </div>

      <div className="flex-1 overflow-y-auto py-1">
        {isLoading && <LoadingSpinner center />}

        {!isLoading && events.length === 0 && (
          <p className="px-3 pt-4 text-sm text-(--text-03)">No activity yet.</p>
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

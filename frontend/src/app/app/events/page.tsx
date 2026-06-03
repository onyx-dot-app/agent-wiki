"use client";

import { Button } from "@onyx-ai/opal/components";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { PageHeader } from "@/components/common/PageHeader";
import { useRequireAuth } from "@/lib/auth";
import { useEvents } from "@/lib/events";
import { formatInTimezone, formatScopePath } from "@/lib/format";
import { useIsMobile } from "@/lib/viewport";

export default function EventsPage() {
  const { user, loading } = useRequireAuth();
  const isMobile = useIsMobile();
  const timezone = user?.settings.timezone ?? "UTC";
  const { events, error, isValidating, refresh } = useEvents({
    kind: "trigger.fire",
    limit: 200,
  });
  const errorMessage = error?.message ?? null;

  if (loading || !user)
    return (
      <main className={isMobile ? "p-4" : "p-8"}>
        <LoadingSpinner center />
      </main>
    );

  return (
    <main style={{ padding: isMobile ? "16px 12px" : "24px 32px" }}>
        <PageHeader
          title="Events"
          description="Trigger fires, newest first."
          actions={
            <Button onClick={() => void refresh()} disabled={isValidating}>
              {isValidating ? "Refreshing…" : "Refresh"}
            </Button>
          }
        />

        {errorMessage && (
          <div className="p-[10px] bg-(--status-error-01) text-(--status-text-error-05) rounded-(--border-radius-04) text-[13px] mb-3">
            {errorMessage}
          </div>
        )}

        {events.length === 0 && !errorMessage && !isValidating && (
          <p className="text-(--text-03) text-sm">No trigger fires yet.</p>
        )}

        <ul className="list-none p-0 m-0">
          {events.map((ev) => {
            const p = ev.payload as {
              doc_path?: string;
              change_kind?: string;
              reason?: string;
              trigger_id?: string;
            };
            return (
              <li
                key={ev.id}
                className="py-[14px] px-4 border border-(--border-01) rounded-(--border-radius-08) mb-[10px] bg-(--background-tint-00)"
              >
                <div className="flex items-baseline justify-between gap-3 mb-[6px]">
                  <div className="font-mono text-xs text-(--text-05)">
                    {p.doc_path ? (
                      <span title={p.doc_path}>{formatScopePath(p.doc_path)}</span>
                    ) : (
                      <em className="text-(--text-02)">(no path)</em>
                    )}
                    {p.change_kind && (
                      <span className="ml-2 py-[1px] px-[6px] bg-(--background-tint-03) text-(--text-05) rounded-(--border-radius-04) text-[10px] font-semibold uppercase">
                        {p.change_kind}
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-(--text-03)">{formatTs(ev.ts, timezone)}</span>
                </div>
                {p.reason && (
                  <div className="text-sm text-(--text-04) whitespace-pre-wrap">
                    {p.reason}
                  </div>
                )}
                <div className="mt-2 text-[11px] text-(--text-02)">
                  trigger {ev.target ?? "?"}
                </div>
              </li>
            );
          })}
        </ul>
    </main>
  );
}

function formatTs(ts: string, timezone: string): string {
  // SQLite's `datetime('now')` is UTC without a Z; treat as UTC.
  const iso = ts.includes("T") ? ts : `${ts.replace(" ", "T")}Z`;
  return formatInTimezone(iso, timezone) || ts;
}

"use client";

import { Button } from "@onyx-ai/opal/components";
import { SvgActivity } from "@onyx-ai/opal/icons";
import { SettingsLayouts } from "@onyx-ai/opal/layouts";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { useRequireAuth } from "@/lib/auth";
import { effectiveTimezone } from "@/lib/cron";
import { useEvents } from "@/lib/events";
import { formatInTimezone, formatScopePath } from "@/lib/format";

export default function ActivitiesPage() {
  const { user, loading } = useRequireAuth();
  const timezone = effectiveTimezone(user?.settings.timezone);
  const { events, error, isValidating, refresh } = useEvents({
    kind: "trigger.fire",
    limit: 200,
  });
  const errorMessage = error?.message ?? null;

  if (loading || !user) return <LoadingSpinner center />;

  return (
    <SettingsLayouts.Root width="lg">
      <SettingsLayouts.Header
        icon={SvgActivity}
        title="Activities"
        description="Trigger fires, newest first."
        rightChildren={
          <Button onClick={() => void refresh()} disabled={isValidating}>
            {isValidating ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />
      <SettingsLayouts.Body>
        {errorMessage && (
          <div className="mb-3 rounded-(--border-radius-04) bg-(--status-error-01) p-[10px] text-[13px] text-(--status-text-error-05)">
            {errorMessage}
          </div>
        )}

        {events.length === 0 && !errorMessage && !isValidating && (
          <p className="text-sm text-(--text-03)">No trigger fires yet.</p>
        )}

        <ul className="m-0 list-none p-0">
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
                className="mb-[10px] rounded-(--border-radius-08) border border-(--border-01) bg-(--background-tint-00) px-4 py-[14px]"
              >
                <div className="mb-[6px] flex items-baseline justify-between gap-3">
                  <div className="font-mono text-xs text-(--text-05)">
                    {p.doc_path ? (
                      <span title={p.doc_path}>
                        {formatScopePath(p.doc_path)}
                      </span>
                    ) : (
                      <em className="text-(--text-02)">(no path)</em>
                    )}
                    {p.change_kind && (
                      <span className="ml-2 rounded-(--border-radius-04) bg-(--background-tint-03) px-[6px] py-[1px] text-[10px] font-semibold text-(--text-05) uppercase">
                        {p.change_kind}
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-(--text-03)">
                    {formatTs(ev.ts, timezone)}
                  </span>
                </div>
                {p.reason && (
                  <div className="text-sm whitespace-pre-wrap text-(--text-04)">
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
      </SettingsLayouts.Body>
    </SettingsLayouts.Root>
  );
}

function formatTs(ts: string, timezone: string): string {
  const iso = ts.includes("T") ? ts : `${ts.replace(" ", "T")}Z`;
  return formatInTimezone(iso, timezone) || ts;
}

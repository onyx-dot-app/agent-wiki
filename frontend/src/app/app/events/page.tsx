"use client";

import { Button } from "@/components/common/Button";
import { PageHeader } from "@/components/common/PageHeader";
import { useRequireAuth } from "@/lib/auth";
import { useEvents } from "@/lib/events";
import { formatInTimezone, formatScopePath } from "@/lib/format";
import { color, radius } from "@/lib/theme";
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

  if (loading || !user) return <main style={{ padding: isMobile ? 16 : 32 }}>Loading…</main>;

  return (
    <main style={{ padding: isMobile ? "16px 12px" : "24px 32px" }}>
        <PageHeader
          title="Events"
          description="Trigger fires, newest first."
          actions={
            <Button onClick={() => void refresh()} disabled={isValidating}>
              {isValidating ? "Loading…" : "Refresh"}
            </Button>
          }
        />

        {errorMessage && (
          <div
            style={{
              padding: 10,
              background: color.state.danger.bg,
              color: color.state.danger.fg,
              borderRadius: radius.sm,
              fontSize: 13,
              marginBottom: 12,
            }}
          >
            {errorMessage}
          </div>
        )}

        {events.length === 0 && !errorMessage && !isValidating && (
          <p style={{ color: color.text.muted, fontSize: 14 }}>No trigger fires yet.</p>
        )}

        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
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
                style={{
                  padding: "14px 16px",
                  border: `1px solid ${color.border.default}`,
                  borderRadius: radius.md,
                  marginBottom: 10,
                  background: color.bg.page,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    justifyContent: "space-between",
                    gap: 12,
                    marginBottom: 6,
                  }}
                >
                  <div
                    style={{
                      fontFamily: "ui-monospace, Menlo, monospace",
                      fontSize: 12,
                      color: color.text.primary,
                    }}
                  >
                    {p.doc_path ? (
                      <span title={p.doc_path}>{formatScopePath(p.doc_path)}</span>
                    ) : (
                      <em style={{ color: color.text.faint }}>(no path)</em>
                    )}
                    {p.change_kind && (
                      <span
                        style={{
                          marginLeft: 8,
                          padding: "1px 6px",
                          background: color.accent.subtleBg,
                          color: color.accent.subtleFg,
                          borderRadius: radius.xs,
                          fontSize: 10,
                          fontWeight: 600,
                          textTransform: "uppercase",
                        }}
                      >
                        {p.change_kind}
                      </span>
                    )}
                  </div>
                  <span style={{ fontSize: 12, color: color.text.muted }}>{formatTs(ev.ts, timezone)}</span>
                </div>
                {p.reason && (
                  <div style={{ fontSize: 14, color: color.text.secondary, whiteSpace: "pre-wrap" }}>
                    {p.reason}
                  </div>
                )}
                <div style={{ marginTop: 8, fontSize: 11, color: color.text.faint }}>
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

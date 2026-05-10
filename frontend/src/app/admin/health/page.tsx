"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/common/AppShell";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { useRequireAuth } from "@/lib/auth";
import { useHealth } from "@/lib/health";
import { color, radius } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";

const POLL_MS = 5000;

const QUEUE_LABELS: Record<string, string> = {
  documents: "Document update processing",
  triggers: "Trigger evaluations",
  lightweight_maintenance: "Lightweight maintenance",
};

export default function AdminHealthPage() {
  const { user, loading } = useRequireAuth();
  const router = useRouter();
  const isMobile = useIsMobile();
  const { health: data, error: healthError, isValidating: healthValidating } = useHealth({
    refreshIntervalMs: POLL_MS,
  });
  const error = healthError?.message ?? null;

  useEffect(() => {
    if (!loading && user && !user.is_admin) router.replace("/");
  }, [loading, user, router]);

  // Track when we last got a *successful* response so the user sees a
  // freshness signal even though SWR doesn't expose `dataUpdatedAt`.
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  useEffect(() => {
    if (!healthValidating && (data || error)) {
      setLastUpdated(new Date());
    }
  }, [data, error, healthValidating]);

  if (loading || !user) return <main style={{ padding: isMobile ? 16 : 32 }}>Loading…</main>;
  if (!user.is_admin) return null;

  const backendUp = !error;
  const statusColor =
    backendUp && data?.status === "ok"
      ? color.state.success.fg
      : color.state.danger.fg;
  const statusLabel = !backendUp
    ? "Backend unreachable"
    : data?.status === "ok"
      ? "Backend OK"
      : "Backend degraded";

  return (
    <AppShell>
      <main style={{ padding: isMobile ? "16px 12px" : "24px 32px", height: "100vh", overflowY: "auto" }}>
        <BackLink />
        <PageHeader
          title="Health"
          description={`Backend liveness and queue depth. Polls every ${POLL_MS / 1000}s.`}
        />

        <section
          style={{
            padding: 16,
            border: `1px solid ${color.border.default}`,
            borderRadius: radius.md,
            background: color.bg.page,
            marginBottom: 20,
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <span
            aria-hidden
            style={{
              width: 12,
              height: 12,
              borderRadius: "50%",
              background: statusColor,
              flexShrink: 0,
            }}
          />
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 14 }}>{statusLabel}</div>
            {error && (
              <div style={{ color: color.state.danger.fg, fontSize: 12, marginTop: 4 }}>{error}</div>
            )}
          </div>
          {lastUpdated && (
            <div style={{ fontSize: 11, color: color.text.faint }}>
              updated {lastUpdated.toLocaleTimeString()}
            </div>
          )}
        </section>

        <h2 style={{ fontSize: 16, fontWeight: 600, margin: "0 0 10px" }}>Queues</h2>
        {!data && !error && <p style={{ color: color.text.muted, fontSize: 13 }}>Loading…</p>}
        {data && (
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {data.queues.map((q) => {
              const haveCounts =
                q.ok && q.ready != null && q.delayed != null && q.in_flight != null;
              // Cap is gated on ready + delayed; in-flight is shown
              // separately because workers already pulled it.
              const pending =
                haveCounts ? (q.ready as number) + (q.delayed as number) : null;
              const pct =
                pending != null && q.limit > 0
                  ? Math.min(100, Math.round((pending / q.limit) * 100))
                  : 0;
              const barColor =
                pct >= 90
                  ? color.state.danger.fg
                  : pct >= 70
                    ? color.state.warning.fg
                    : color.accent.bg;
              return (
                <li
                  key={q.name}
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
                      justifyContent: "space-between",
                      alignItems: "baseline",
                      marginBottom: 8,
                    }}
                  >
                    <div style={{ fontSize: 14 }}>{QUEUE_LABELS[q.name] ?? q.name}</div>
                    <div style={{ fontSize: 12, color: color.text.secondary }}>
                      {haveCounts && pending != null ? (
                        <>
                          <strong>{pending.toLocaleString()}</strong> /{" "}
                          {q.limit.toLocaleString()} ({pct}%)
                        </>
                      ) : (
                        <span style={{ color: color.state.danger.fg }}>{q.error ?? "unknown"}</span>
                      )}
                    </div>
                  </div>
                  <div
                    style={{
                      height: 6,
                      background: color.bg.sunken,
                      borderRadius: radius.xs,
                      overflow: "hidden",
                      marginBottom: haveCounts ? 8 : 0,
                    }}
                  >
                    <div
                      style={{
                        width: `${pct}%`,
                        height: "100%",
                        background: barColor,
                        transition: "width 200ms ease",
                      }}
                    />
                  </div>
                  {haveCounts && (
                    <div
                      style={{
                        display: "flex",
                        gap: 16,
                        fontSize: 11,
                        color: color.text.muted,
                      }}
                    >
                      <span>
                        ready <strong style={{ color: color.text.primary }}>{q.ready}</strong>
                      </span>
                      <span title="Tasks scheduled for a future run time — waiting their turn, not stuck.">
                        scheduled <strong style={{ color: color.text.primary }}>{q.delayed}</strong>
                      </span>
                      <span>
                        in flight{" "}
                        <strong style={{ color: color.text.primary }}>{q.in_flight}</strong>
                      </span>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </main>
    </AppShell>
  );
}

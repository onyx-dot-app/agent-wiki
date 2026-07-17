"use client";

import { useEffect, useState } from "react";

import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { useHealth } from "@/lib/health";
import { useIsMobile } from "@/lib/viewport";

const POLL_MS = 5000;

const QUEUE_LABELS: Record<string, string> = {
  documents: "Document update processing",
  triggers: "Trigger evaluations",
  lightweight_maintenance: "Lightweight maintenance",
};

export default function AdminHealthPage() {
  const isMobile = useIsMobile();
  const {
    health: data,
    error: healthError,
    isValidating: healthValidating,
  } = useHealth({
    refreshIntervalMs: POLL_MS,
  });
  const error = healthError?.message ?? null;

  // Track when we last got a *successful* response so the user sees a
  // freshness signal even though SWR doesn't expose `dataUpdatedAt`.
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  useEffect(() => {
    if (!healthValidating && (data || error)) {
      setLastUpdated(new Date());
    }
  }, [data, error, healthValidating]);

  const backendUp = !error;
  const statusColor =
    backendUp && data?.status === "ok"
      ? "var(--status-text-success-05)"
      : "var(--status-text-error-05)";
  const statusLabel = !backendUp
    ? "Backend unreachable"
    : data?.status === "ok"
      ? "Backend OK"
      : "Backend degraded";

  return (
    <RequireAdmin>
      <main
        style={{ padding: isMobile ? "16px 12px" : "24px 32px" }}
        className="h-screen overflow-y-auto"
      >
        <BackLink />
        <PageHeader
          title="Health"
          description={`Backend liveness and queue depth. Polls every ${POLL_MS / 1000}s.`}
        />

        <section className="mb-5 flex items-center gap-3 rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-00) p-4">
          <span
            aria-hidden
            style={{ background: statusColor }}
            className="h-3 w-3 shrink-0 rounded-full"
          />
          <div className="flex-1">
            <div className="text-sm font-semibold">{statusLabel}</div>
            {error && (
              <div className="mt-1 text-xs text-(--status-text-error-05)">
                {error}
              </div>
            )}
          </div>
          {lastUpdated && (
            <div className="text-[11px] text-(--text-02)">
              updated {lastUpdated.toLocaleTimeString()}
            </div>
          )}
        </section>

        <h2 className="m-0 mb-[10px] text-base font-semibold">Queues</h2>
        {!data && !error && <LoadingSpinner />}
        {data && (
          <ul className="m-0 list-none p-0">
            {data.queues.map((q) => {
              const haveCounts =
                q.ok &&
                q.ready != null &&
                q.delayed != null &&
                q.in_flight != null;
              // Cap is gated on ready + delayed; in-flight is shown
              // separately because workers already pulled it.
              const pending = haveCounts
                ? (q.ready as number) + (q.delayed as number)
                : null;
              const pct =
                pending != null && q.limit > 0
                  ? Math.min(100, Math.round((pending / q.limit) * 100))
                  : 0;
              const barColor =
                pct >= 90
                  ? "var(--status-text-error-05)"
                  : pct >= 70
                    ? "var(--status-text-warning-05)"
                    : "var(--background-tint-inverted-00)";
              return (
                <li
                  key={q.name}
                  className="mb-[10px] rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-00) px-4 py-[14px]"
                >
                  <div className="mb-2 flex items-baseline justify-between">
                    <div className="text-sm">
                      {QUEUE_LABELS[q.name] ?? q.name}
                    </div>
                    <div className="text-xs text-(--text-04)">
                      {haveCounts && pending != null ? (
                        <>
                          <strong>{pending.toLocaleString()}</strong> /{" "}
                          {q.limit.toLocaleString()} ({pct}%)
                        </>
                      ) : (
                        <span className="text-(--status-text-error-05)">
                          {q.error ?? "unknown"}
                        </span>
                      )}
                    </div>
                  </div>
                  <div
                    style={{ marginBottom: haveCounts ? 8 : 0 }}
                    className="h-[6px] overflow-hidden rounded-(--radius-04) bg-(--background-tint-02)"
                  >
                    <div
                      style={{
                        width: `${pct}%`,
                        background: barColor,
                      }}
                      className="h-full transition-[width] duration-200 ease-[ease]"
                    />
                  </div>
                  {haveCounts && (
                    <div className="flex gap-4 text-[11px] text-(--text-03)">
                      <span>
                        ready{" "}
                        <strong className="text-(--text-05)">{q.ready}</strong>
                      </span>
                      <span title="Tasks scheduled for a future run time — waiting their turn, not stuck.">
                        scheduled{" "}
                        <strong className="text-(--text-05)">
                          {q.delayed}
                        </strong>
                      </span>
                      <span>
                        in flight{" "}
                        <strong className="text-(--text-05)">
                          {q.in_flight}
                        </strong>
                      </span>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </main>
    </RequireAdmin>
  );
}

"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/common/AppShell";
import { useRequireAuth } from "@/lib/auth";
import { useHealth } from "@/lib/health";

const POLL_MS = 5000;

const QUEUE_LABELS: Record<string, string> = {
  documents: "Document update processing",
  triggers: "Trigger evaluations",
  wiki_bm25: "Wiki page indexing",
};

export default function HealthPage() {
  const { user, loading } = useRequireAuth();
  const { health: data, error: healthError, isValidating: healthValidating } = useHealth({
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

  if (loading || !user) return <main style={{ padding: 32 }}>Loading…</main>;

  const backendUp = !error;
  const statusColor = backendUp && data?.status === "ok" ? "#16a34a" : "#dc2626";
  const statusLabel = !backendUp
    ? "Backend unreachable"
    : data?.status === "ok"
      ? "Backend OK"
      : "Backend degraded";

  return (
    <AppShell>
      <main style={{ padding: "24px 32px", height: "100vh", overflowY: "auto" }}>
        <header style={{ marginBottom: 20 }}>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>Health</h1>
          <p style={{ color: "#666", margin: "4px 0 0", fontSize: 13 }}>
            Backend liveness and queue depth. Polls every {POLL_MS / 1000}s.
          </p>
        </header>

        <section
          style={{
            padding: 16,
            border: "1px solid #e5e7eb",
            borderRadius: 8,
            background: "white",
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
              <div style={{ color: "#991b1b", fontSize: 12, marginTop: 4 }}>{error}</div>
            )}
          </div>
          {lastUpdated && (
            <div style={{ fontSize: 11, color: "#9ca3af" }}>
              updated {lastUpdated.toLocaleTimeString()}
            </div>
          )}
        </section>

        <h2 style={{ fontSize: 16, fontWeight: 600, margin: "0 0 10px" }}>Queues</h2>
        {!data && !error && <p style={{ color: "#888", fontSize: 13 }}>Loading…</p>}
        {data && (
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {data.queues.map((q) => {
              const pct =
                q.size != null && q.limit > 0
                  ? Math.min(100, Math.round((q.size / q.limit) * 100))
                  : 0;
              const barColor = pct >= 90 ? "#dc2626" : pct >= 70 ? "#d97706" : "#2563eb";
              return (
                <li
                  key={q.name}
                  style={{
                    padding: "14px 16px",
                    border: "1px solid #e5e7eb",
                    borderRadius: 8,
                    marginBottom: 10,
                    background: "white",
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
                    <div style={{ fontSize: 12, color: "#374151" }}>
                      {q.ok && q.size != null ? (
                        <>
                          <strong>{q.size.toLocaleString()}</strong> /{" "}
                          {q.limit.toLocaleString()} ({pct}%)
                        </>
                      ) : (
                        <span style={{ color: "#991b1b" }}>{q.error ?? "unknown"}</span>
                      )}
                    </div>
                  </div>
                  <div
                    style={{
                      height: 6,
                      background: "#f1f5f9",
                      borderRadius: 3,
                      overflow: "hidden",
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
                </li>
              );
            })}
          </ul>
        )}
      </main>
    </AppShell>
  );
}

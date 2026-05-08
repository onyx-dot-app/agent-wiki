"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/common/AppShell";
import { useRequireAuth } from "@/lib/auth";
import { fetchHealth, type HealthResponse } from "@/lib/health";

const POLL_MS = 5000;

export default function HealthPage() {
  const { user, loading } = useRequireAuth();
  const [data, setData] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetchHealth();
      setData(res);
      setError(null);
      setLastUpdated(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to reach backend");
      setData(null);
      setLastUpdated(new Date());
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    void refresh();
    const tick = () => {
      timer.current = setTimeout(async () => {
        await refresh();
        tick();
      }, POLL_MS);
    };
    tick();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [user, refresh]);

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
                    <div style={{ fontFamily: "ui-monospace, Menlo, monospace", fontSize: 14 }}>
                      {q.name}
                    </div>
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

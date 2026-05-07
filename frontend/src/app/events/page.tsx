"use client";

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/common/AppShell";
import { useRequireAuth } from "@/lib/auth";
import { listEvents, type AppEvent } from "@/lib/events";

export default function EventsPage() {
  const { user, loading } = useRequireAuth();
  const [events, setEvents] = useState<AppEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setEvents(await listEvents({ kind: "trigger.fire", limit: 200 }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load events");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (user) void refresh();
  }, [user, refresh]);

  if (loading || !user) return <main style={{ padding: 32 }}>Loading…</main>;

  return (
    <AppShell>
      <main style={{ padding: "24px 32px", height: "100vh", overflowY: "auto" }}>
        <header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 16,
          }}
        >
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>Events</h1>
            <p style={{ color: "#666", margin: "4px 0 0", fontSize: 13 }}>
              Trigger fires, newest first.
            </p>
          </div>
          <button onClick={refresh} disabled={busy} style={secondaryBtn}>
            {busy ? "Loading…" : "Refresh"}
          </button>
        </header>

        {error && (
          <div
            style={{
              padding: 10,
              background: "#fef2f2",
              color: "#991b1b",
              borderRadius: 6,
              fontSize: 13,
              marginBottom: 12,
            }}
          >
            {error}
          </div>
        )}

        {events.length === 0 && !error && !busy && (
          <p style={{ color: "#888", fontSize: 14 }}>No trigger fires yet.</p>
        )}

        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {events.map((ev) => {
            const p = ev.payload as {
              doc_path?: string;
              change_kind?: string;
              reason?: string;
              trigger_id?: string;
              sha?: string;
            };
            return (
              <li
                key={ev.id}
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
                      color: "#111",
                    }}
                  >
                    {p.doc_path ?? <em style={{ color: "#9ca3af" }}>(no path)</em>}
                    {p.change_kind && (
                      <span
                        style={{
                          marginLeft: 8,
                          padding: "1px 6px",
                          background: "#eef2ff",
                          color: "#3730a3",
                          borderRadius: 4,
                          fontSize: 10,
                          fontWeight: 600,
                          textTransform: "uppercase",
                        }}
                      >
                        {p.change_kind}
                      </span>
                    )}
                  </div>
                  <span style={{ fontSize: 12, color: "#6b7280" }}>{formatTs(ev.ts)}</span>
                </div>
                {p.reason && (
                  <div style={{ fontSize: 14, color: "#374151", whiteSpace: "pre-wrap" }}>
                    {p.reason}
                  </div>
                )}
                <div style={{ marginTop: 8, fontSize: 11, color: "#9ca3af" }}>
                  trigger {ev.target ?? "?"} · sha {p.sha?.slice(0, 7) ?? "?"}
                </div>
              </li>
            );
          })}
        </ul>
      </main>
    </AppShell>
  );
}

function formatTs(ts: string): string {
  // SQLite's `datetime('now')` is UTC without a Z; treat as UTC.
  const iso = ts.includes("T") ? ts : `${ts.replace(" ", "T")}Z`;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

const secondaryBtn: React.CSSProperties = {
  padding: "6px 12px",
  background: "transparent",
  color: "#374151",
  border: "1px solid #ddd",
  borderRadius: 6,
  cursor: "pointer",
  fontSize: 13,
};

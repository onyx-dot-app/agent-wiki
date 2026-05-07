"use client";

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/common/AppShell";
import { TriggerModal } from "@/components/triggers/TriggerModal";
import { useRequireAuth } from "@/lib/auth";
import {
  deleteTrigger,
  listTriggers,
  updateTrigger,
  type Trigger,
} from "@/lib/triggers";

export default function TriggersPage() {
  const { user, loading } = useRequireAuth();
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Trigger | null>(null);

  const refresh = useCallback(async () => {
    try {
      setTriggers(await listTriggers());
      setListError(null);
    } catch (e) {
      setListError(e instanceof Error ? e.message : "failed to load triggers");
    }
  }, []);

  useEffect(() => {
    if (user) void refresh();
  }, [user, refresh]);

  if (loading || !user) return <main style={{ padding: 32 }}>Loading…</main>;

  async function onToggle(t: Trigger) {
    setBusyId(t.id);
    try {
      const updated = await updateTrigger(t.id, { enabled: !t.enabled });
      setTriggers((prev) => prev.map((x) => (x.id === t.id ? updated : x)));
    } catch (e) {
      setListError(e instanceof Error ? e.message : "toggle failed");
    } finally {
      setBusyId(null);
    }
  }

  async function onDelete(t: Trigger) {
    if (!confirm(`Delete this trigger?\n\n"${t.nl_description}"`)) return;
    setBusyId(t.id);
    try {
      await deleteTrigger(t.id);
      setTriggers((prev) => prev.filter((x) => x.id !== t.id));
    } catch (e) {
      setListError(e instanceof Error ? e.message : "delete failed");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <AppShell>
      <main style={{ padding: "24px 32px", height: "100vh", overflowY: "auto" }}>
        <header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 20,
          }}
        >
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>Triggers</h1>
          <button
            onClick={() => {
              setEditing(null);
              setModalOpen(true);
            }}
            style={primaryBtn}
          >
            + New trigger
          </button>
        </header>

        <p style={{ color: "#6b7280", fontSize: 13, marginTop: 0, marginBottom: 16 }}>
          Triggers fire when a doc within their scope changes. On a match, an
          event is recorded in the Events tab. v0 has no outbound dispatch.
        </p>

        {listError && (
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
            {listError}
          </div>
        )}

        {triggers.length === 0 && !listError && (
          <p style={{ color: "#6b7280", fontSize: 14 }}>
            No triggers yet. Create one to start watching docs for changes.
          </p>
        )}

        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {triggers.map((t) => (
            <li
              key={t.id}
              style={{
                padding: "14px 16px",
                border: "1px solid #e5e7eb",
                borderRadius: 8,
                marginBottom: 10,
                background: "white",
                opacity: busyId === t.id ? 0.6 : 1,
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  justifyContent: "space-between",
                  gap: 12,
                }}
              >
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div
                    style={{
                      fontFamily: "ui-monospace, Menlo, monospace",
                      fontSize: 12,
                      color: "#6b7280",
                      marginBottom: 4,
                    }}
                  >
                    {t.scope_path}
                  </div>
                  <div style={{ fontSize: 14, color: "#111", whiteSpace: "pre-wrap" }}>
                    {t.nl_description}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexShrink: 0 }}>
                  <span
                    style={{
                      fontSize: 11,
                      padding: "2px 8px",
                      borderRadius: 999,
                      background: t.enabled ? "#dcfce7" : "#f3f4f6",
                      color: t.enabled ? "#166534" : "#6b7280",
                      fontWeight: 600,
                    }}
                  >
                    {t.enabled ? "ENABLED" : "DISABLED"}
                  </span>
                  <button
                    onClick={() => onToggle(t)}
                    disabled={busyId === t.id}
                    style={iconBtn}
                  >
                    {t.enabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    onClick={() => {
                      setEditing(t);
                      setModalOpen(true);
                    }}
                    disabled={busyId === t.id}
                    style={iconBtn}
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => onDelete(t)}
                    disabled={busyId === t.id}
                    style={{ ...iconBtn, color: "#dc2626", borderColor: "#fecaca" }}
                  >
                    Delete
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>

        <TriggerModal
          open={modalOpen}
          initial={editing ?? undefined}
          onClose={() => {
            setModalOpen(false);
            setEditing(null);
          }}
          onSaved={(saved) => {
            setTriggers((prev) => {
              const i = prev.findIndex((t) => t.id === saved.id);
              if (i === -1) return [saved, ...prev];
              const next = prev.slice();
              next[i] = saved;
              return next;
            });
          }}
        />
      </main>
    </AppShell>
  );
}

const primaryBtn: React.CSSProperties = {
  padding: "8px 14px",
  background: "#6366f1",
  color: "white",
  border: "none",
  borderRadius: 8,
  cursor: "pointer",
  fontWeight: 600,
  fontSize: 13,
};

const iconBtn: React.CSSProperties = {
  padding: "5px 10px",
  background: "white",
  color: "#374151",
  border: "1px solid #e5e7eb",
  borderRadius: 6,
  cursor: "pointer",
  fontSize: 12,
};

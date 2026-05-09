"use client";

import { useState } from "react";

import { AppShell } from "@/components/common/AppShell";
import { TriggerHistoryModal } from "@/components/triggers/TriggerHistoryModal";
import { TriggerModal } from "@/components/triggers/TriggerModal";
import { useRequireAuth } from "@/lib/auth";
import {
  deleteTrigger,
  formatScopePath,
  getTriggerVersion,
  updateTrigger,
  useTriggers,
  type Trigger,
} from "@/lib/triggers";

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const diffMs = Date.now() - t;
  const sec = Math.round(diffMs / 1000);
  if (sec < 60) return "just now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} hr ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day} day${day === 1 ? "" : "s"} ago`;
  return new Date(iso).toLocaleDateString();
}

export default function TriggersPage() {
  const { user, loading } = useRequireAuth();
  const { triggers, error: listSwrError, refresh } = useTriggers();
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Trigger | null>(null);
  const [historyFor, setHistoryFor] = useState<Trigger | null>(null);

  const listError = mutationError ?? listSwrError?.message ?? null;

  if (loading || !user) return <main style={{ padding: 32 }}>Loading…</main>;

  async function onToggle(t: Trigger) {
    setBusyId(t.id);
    setMutationError(null);
    try {
      const updated = await updateTrigger(t.id, { enabled: !t.enabled });
      // Optimistic update: patch the cached list, then revalidate.
      await refresh(
        (cur) => ({
          triggers: (cur?.triggers ?? []).map((x) => (x.id === t.id ? updated : x)),
        }),
        { revalidate: true },
      );
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "toggle failed");
    } finally {
      setBusyId(null);
    }
  }

  async function onDelete(t: Trigger) {
    if (!confirm(`Delete this trigger?\n\n"${t.nl_description}"`)) return;
    setBusyId(t.id);
    setMutationError(null);
    try {
      await deleteTrigger(t.id);
      await refresh(
        (cur) => ({
          triggers: (cur?.triggers ?? []).filter((x) => x.id !== t.id),
        }),
        { revalidate: true },
      );
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "delete failed");
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

        <p style={{ color: "#6b7280", fontSize: 13, marginTop: 0, marginBottom: 16, lineHeight: 1.55 }}>
          Triggers watch a doc (or folder) and notice when something specific
          changes. When that happens, the message you wrote shows up on the
          Events tab so you can review it.
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
                      display: "flex",
                      gap: 10,
                      alignItems: "baseline",
                    }}
                  >
                    <span title={t.scope_path}>{formatScopePath(t.scope_path)}</span>
                    {t.last_edited_at && (
                      <span
                        title={new Date(t.last_edited_at).toLocaleString()}
                        style={{ fontFamily: "inherit", fontSize: 11, color: "#9ca3af" }}
                      >
                        edited {formatRelative(t.last_edited_at)}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 14, color: "#111", whiteSpace: "pre-wrap" }}>
                    <span style={{ color: "#92400e", fontWeight: 600 }}>If</span> {t.nl_description}
                    {t.message && (
                      <>
                        {"\n"}
                        <span style={{ color: "#047857", fontWeight: 600 }}>then send</span>{" "}
                        {t.message}
                      </>
                    )}
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
                    onClick={() => setHistoryFor(t)}
                    disabled={busyId === t.id}
                    style={iconBtn}
                  >
                    History
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
            void refresh(
              (cur) => {
                const prev = cur?.triggers ?? [];
                const i = prev.findIndex((t) => t.id === saved.id);
                if (i === -1) return { triggers: [saved, ...prev] };
                const next = prev.slice();
                next[i] = saved;
                return { triggers: next };
              },
              { revalidate: true },
            );
          }}
        />

        <TriggerHistoryModal
          trigger={historyFor}
          onClose={() => setHistoryFor(null)}
          onSelectVersion={async (sha) => {
            if (!historyFor) return;
            try {
              const version = await getTriggerVersion(historyFor.id, sha);
              setEditing({
                ...historyFor,
                scope_path: version.scope_path,
                nl_description: version.nl_description,
                message: version.message,
                destination: version.destination,
                enabled: version.enabled,
              });
              setHistoryFor(null);
              setModalOpen(true);
            } catch (e) {
              setMutationError(e instanceof Error ? e.message : "failed to load version");
            }
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

"use client";

import { useState, type CSSProperties } from "react";

import { AppShell } from "@/components/common/AppShell";
import { Button } from "@/components/common/Button";
import { PageHeader } from "@/components/common/PageHeader";
import { TriggerHistoryModal } from "@/components/triggers/TriggerHistoryModal";
import { TriggerModal } from "@/components/triggers/TriggerModal";
import { useRequireAuth } from "@/lib/auth";
import { color, radius } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";
import { describeCron } from "@/lib/cron";
import { formatScopePath } from "@/lib/format";
import {
  deleteTrigger,
  getTriggerVersion,
  updateTrigger,
  useTriggerDestinations,
  useTriggers,
  type Trigger,
} from "@/lib/triggers";

const sentenceTagStyle: CSSProperties = {
  flexShrink: 0,
  fontSize: 10,
  fontWeight: 600,
  padding: "1px 6px",
  borderRadius: radius.xs,
  background: color.accent.subtleBg,
  color: color.accent.subtleFg,
  textTransform: "uppercase",
  letterSpacing: 0.3,
};

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
  const isMobile = useIsMobile();
  const { triggers, error: listSwrError, refresh } = useTriggers();
  const destinations = useTriggerDestinations();
  const destinationLabel = (id: string | null | undefined) =>
    destinations.find((d) => d.id === id)?.name ?? id ?? "—";
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Trigger | null>(null);
  const [historyFor, setHistoryFor] = useState<Trigger | null>(null);

  const listError = mutationError ?? listSwrError?.message ?? null;

  if (loading || !user) return <main style={{ padding: isMobile ? 16 : 32 }}>Loading…</main>;

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
      <main style={{ padding: isMobile ? "16px 12px" : "24px 32px", height: "100vh", overflowY: "auto" }}>
        <PageHeader
          title="Triggers"
          description="Triggers watch a document (or folder) and notice when something specific changes, or check on a recurring schedule. When the trigger fires, the message you wrote shows up on the Events tab so you can review it."
          actions={
            <Button
              variant="primary"
              onClick={() => {
                setEditing(null);
                setModalOpen(true);
              }}
            >
              + New trigger
            </Button>
          }
        />

        {listError && (
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
            {listError}
          </div>
        )}

        {triggers.length === 0 && !listError && (
          <p style={{ color: color.text.muted, fontSize: 14 }}>
            No triggers yet. Create one to start watching documents for changes.
          </p>
        )}

        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {triggers.map((t) => (
            <li
              key={t.id}
              style={{
                padding: "14px 16px",
                border: `1px solid ${color.border.default}`,
                borderRadius: radius.md,
                marginBottom: 10,
                background: color.bg.page,
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
                      color: color.text.muted,
                      marginBottom: 4,
                      display: "flex",
                      gap: 10,
                      alignItems: "baseline",
                    }}
                  >
                    <span title={t.scope_path}>{formatScopePath(t.scope_path)}</span>
                    <span style={{ fontFamily: "inherit", fontSize: 11, color: color.text.faint }}>
                      {t.id}
                    </span>
                    {t.last_edited_at && (
                      <span
                        title={new Date(t.last_edited_at).toLocaleString()}
                        style={{ fontFamily: "inherit", fontSize: 11, color: color.text.faint }}
                      >
                        edited {formatRelative(t.last_edited_at)}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 14, color: color.text.primary, lineHeight: 1.55 }}>
                    {t.kind === "schedule" && (
                      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
                        <span style={sentenceTagStyle}>WHEN</span>
                        <span style={{ flex: 1, minWidth: 0 }}>
                          {describeCron(t.schedule_cron, t.schedule_timezone)}
                          {t.schedule_start_at && (
                            <>
                              {" "}
                              <span style={{ color: color.text.muted, fontSize: 12 }}>
                                · starting {new Date(t.schedule_start_at).toLocaleString()}
                              </span>
                            </>
                          )}
                          {t.schedule_last_fired_at && (
                            <>
                              {" "}
                              <span style={{ color: color.text.faint, fontSize: 12 }}>
                                · last fired {formatRelative(t.schedule_last_fired_at)}
                              </span>
                            </>
                          )}
                        </span>
                      </div>
                    )}
                    <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                      <span style={sentenceTagStyle}>IF</span>
                      <span style={{ flex: 1, minWidth: 0 }}>{t.nl_description}</span>
                    </div>
                    {t.message && (
                      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 6 }}>
                        <span style={sentenceTagStyle}>THEN SEND</span>
                        <span style={{ flex: 1, minWidth: 0 }}>{t.message}</span>
                      </div>
                    )}
                    <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 6 }}>
                      <span style={sentenceTagStyle}>TO</span>
                      <span style={{ flex: 1, minWidth: 0, color: color.text.secondary }}>
                        {destinationLabel(t.destination)}
                      </span>
                    </div>
                  </div>
                </div>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexShrink: 0 }}>
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      fontSize: 11,
                      padding: "2px 8px",
                      borderRadius: radius.pill,
                      background: t.enabled ? color.accent.subtleBg : color.bg.sunken,
                      color: t.enabled ? color.accent.subtleFg : color.text.muted,
                      border: `1px solid ${t.enabled ? color.accent.subtleBorder : color.border.default}`,
                      fontWeight: 600,
                      letterSpacing: 0.3,
                    }}
                  >
                    <span
                      aria-hidden
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: "50%",
                        background: t.enabled ? color.state.success.fg : color.text.faint,
                      }}
                    />
                    {t.enabled ? "ENABLED" : "DISABLED"}
                  </span>
                  <Button size="sm" onClick={() => onToggle(t)} disabled={busyId === t.id}>
                    {t.enabled ? "Disable" : "Enable"}
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => {
                      setEditing(t);
                      setModalOpen(true);
                    }}
                    disabled={busyId === t.id}
                  >
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => setHistoryFor(t)}
                    disabled={busyId === t.id}
                  >
                    History
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => onDelete(t)}
                    disabled={busyId === t.id}
                  >
                    Delete
                  </Button>
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
                kind: version.kind ?? historyFor.kind,
                schedule_cron: version.schedule_cron,
                schedule_timezone: version.schedule_timezone,
                schedule_start_at: version.schedule_start_at,
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

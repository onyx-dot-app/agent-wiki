"use client";

import { useState, type CSSProperties } from "react";

import { Button } from "@/components/common/Button";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { PageHeader } from "@/components/common/PageHeader";
import { TriggerHistoryModal } from "@/components/triggers/TriggerHistoryModal";
import { TriggerModal } from "@/components/triggers/TriggerModal";
import { useRequireAuth } from "@/lib/auth";
import { color, radius } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";
import { describeCron } from "@/lib/cron";
import { formatScopePath } from "@/lib/format";
import {
  createSlackWebhook,
  deleteSlackWebhook,
  deleteTrigger,
  getTriggerVersion,
  updateTrigger,
  useSlackWebhooks,
  useTriggerDestinations,
  useTriggers,
  type Trigger,
} from "@/lib/triggers";
import { ApiError } from "@/lib/api";

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
  const { webhooks: slackWebhooks } = useSlackWebhooks();
  const destinationLabel = (t: Trigger) => {
    // For Slack, name the specific channel rather than the generic kind.
    if (t.destination === "slack") {
      const ch = slackWebhooks.find((w) => w.id === t.slack_webhook_id);
      return ch ? `Slack · ${ch.name}` : "Slack · (channel removed)";
    }
    return (
      destinations.find((d) => d.id === t.destination)?.name ??
      t.destination ??
      "—"
    );
  };
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Trigger | null>(null);
  const [historyFor, setHistoryFor] = useState<Trigger | null>(null);

  const listError = mutationError ?? listSwrError?.message ?? null;

  if (loading || !user)
    return (
      <main style={{ padding: isMobile ? 16 : 32 }}>
        <LoadingSpinner center />
      </main>
    );

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
    <main style={{ padding: isMobile ? "16px 12px" : "24px 32px" }}>
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
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12,
                  flexWrap: "wrap",
                  marginBottom: 10,
                }}
              >
                <div
                  style={{
                    fontFamily: "ui-monospace, Menlo, monospace",
                    fontSize: 12,
                    color: color.text.muted,
                    display: "flex",
                    gap: 10,
                    alignItems: "baseline",
                    flexWrap: "wrap",
                    minWidth: 0,
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
                <div
                  style={{
                    display: "flex",
                    gap: 6,
                    alignItems: "center",
                    flexShrink: 0,
                    flexWrap: "wrap",
                  }}
                >
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
                    {destinationLabel(t)}
                  </span>
                </div>
              </div>
            </li>
          ))}
        </ul>

        <SlackChannelsCard />

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
  );
}

// --------------------------------------------------------------------------- //
// Slack channels — per-user named webhooks a trigger can post to.             //
// --------------------------------------------------------------------------- //

function SlackChannelsCard() {
  const { webhooks, error, isLoading, refresh } = useSlackWebhooks();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function onAdd() {
    if (!name.trim() || !url.trim()) return;
    setBusy(true);
    setFormError(null);
    try {
      await createSlackWebhook(name.trim(), url.trim());
      await refresh();
      setAdding(false);
      setName("");
      setUrl("");
    } catch (e) {
      setFormError(e instanceof ApiError ? e.message : "failed to add channel");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(id: string, label: string) {
    if (!confirm(`Delete "${label}"? Triggers posting to it will stop delivering to Slack.`)) {
      return;
    }
    try {
      await deleteSlackWebhook(id);
      await refresh();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "failed to delete");
    }
  }

  return (
    <section
      style={{
        marginTop: 28,
        padding: 16,
        border: `1px solid ${color.border.default}`,
        borderRadius: radius.md,
        background: color.bg.panel,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 4,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 16 }}>Slack channels</h2>
        {!adding && (
          <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
            + Add channel
          </Button>
        )}
      </div>
      <p style={{ margin: "0 0 12px", fontSize: 13, color: color.text.muted }}>
        Incoming webhooks you can point a trigger at. Create one in Slack (Apps → Incoming
        Webhooks), then pick it as a trigger&apos;s destination. Private to you.
      </p>

      {error && (
        <div style={{ color: color.state.danger.fg, fontSize: 13, marginBottom: 8 }}>
          {error.message || "Failed to load channels."}
        </div>
      )}

      {adding && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            padding: 12,
            marginBottom: 12,
            border: `1px solid ${color.border.default}`,
            borderRadius: radius.sm,
            background: color.bg.sunken,
          }}
        >
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Channel name (e.g. PM Standup)"
            disabled={busy}
            maxLength={80}
            style={channelInputStyle}
          />
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://hooks.slack.com/services/…"
            disabled={busy}
            style={channelInputStyle}
          />
          {formError && (
            <div style={{ color: color.state.danger.fg, fontSize: 13 }}>{formError}</div>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <Button
              variant="primary"
              size="sm"
              disabled={busy || !name.trim() || !url.trim()}
              onClick={() => void onAdd()}
            >
              {busy ? "Adding…" : "Add"}
            </Button>
            <Button size="sm" disabled={busy} onClick={() => setAdding(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {isLoading && webhooks.length === 0 && !error && <LoadingSpinner />}

      {!isLoading && webhooks.length === 0 && !adding && (
        <p style={{ color: color.text.muted, fontSize: 14, margin: 0 }}>
          No channels yet — add one to deliver trigger fires to Slack.
        </p>
      )}

      {webhooks.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {webhooks.map((w) => (
            <li
              key={w.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "10px 12px",
                border: `1px solid ${color.border.default}`,
                borderRadius: radius.sm,
                marginTop: 8,
                background: color.bg.page,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 500, fontSize: 14, color: color.text.primary }}>
                  {w.name}
                </div>
                <div
                  style={{
                    fontSize: 12,
                    color: color.text.muted,
                    marginTop: 2,
                    fontFamily: "ui-monospace, monospace",
                  }}
                >
                  {w.webhook_url_hint}
                </div>
              </div>
              <Button size="sm" variant="danger" onClick={() => void onDelete(w.id, w.name)}>
                Delete
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const channelInputStyle: CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  boxSizing: "border-box",
  border: `1px solid ${color.border.default}`,
  borderRadius: radius.sm,
  fontSize: 14,
};

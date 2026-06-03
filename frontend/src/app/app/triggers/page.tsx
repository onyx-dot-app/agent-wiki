"use client";

import { useState } from "react";

import { Button } from "@onyx-ai/opal/components";
import { SvgPlus } from "@onyx-ai/opal/icons";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { PageHeader } from "@/components/common/PageHeader";
import { TriggerHistoryModal } from "@/components/triggers/TriggerHistoryModal";
import { TriggerModal } from "@/components/triggers/TriggerModal";
import { useRequireAuth } from "@/lib/auth";
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

const sentenceTagCn =
  "shrink-0 text-[10px] font-semibold px-[6px] py-[1px] rounded-(--radius-xs) bg-(--color-accent-subtle-bg) text-(--color-accent-subtle-fg) uppercase tracking-[0.3px]";

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
      <main className={isMobile ? "p-4" : "p-8"}>
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
    <main className={isMobile ? "py-4 px-3" : "py-6 px-8"}>
        <PageHeader
          title="Triggers"
          description="Triggers watch a document (or folder) and notice when something specific changes, or check on a recurring schedule. When the trigger fires, the message you wrote shows up on the Events tab so you can review it."
          actions={
            <Button
              variant="action"
              icon={SvgPlus}
              onClick={() => {
                setEditing(null);
                setModalOpen(true);
              }}
            >
              New trigger
            </Button>
          }
        />

        {listError && (
          <div className="p-[10px] bg-(--color-state-danger-bg) text-(--color-state-danger-fg) rounded-(--radius-sm) text-[13px] mb-3">
            {listError}
          </div>
        )}

        {triggers.length === 0 && !listError && (
          <p className="text-(--color-text-muted) text-sm">
            No triggers yet. Create one to start watching documents for changes.
          </p>
        )}

        <ul className="list-none p-0 m-0">
          {triggers.map((t) => (
            <li
              key={t.id}
              className={`py-[14px] px-4 border border-(--color-border-default) rounded-(--radius-md) mb-[10px] bg-(--color-bg-page) ${busyId === t.id ? "opacity-60" : "opacity-100"}`}
            >
              <div className="flex items-center justify-between gap-3 flex-wrap mb-[10px]">
                <div className="font-mono text-xs text-(--color-text-muted) flex gap-[10px] items-baseline flex-wrap min-w-0">
                  <span title={t.scope_path}>{formatScopePath(t.scope_path)}</span>
                  <span className="text-[11px] text-(--color-text-faint)">
                    {t.id}
                  </span>
                  {t.last_edited_at && (
                    <span
                      title={new Date(t.last_edited_at).toLocaleString()}
                      className="text-[11px] text-(--color-text-faint)"
                    >
                      edited {formatRelative(t.last_edited_at)}
                    </span>
                  )}
                </div>
                <div className="flex gap-1.5 items-center shrink-0 flex-wrap">
                  <span
                    className={`inline-flex items-center gap-1.5 text-[11px] py-[2px] px-2 rounded-full font-semibold tracking-[0.3px] border ${t.enabled ? "bg-(--color-accent-subtle-bg) text-(--color-accent-subtle-fg) border-(--color-accent-subtle-border)" : "bg-(--color-bg-sunken) text-(--color-text-muted) border-(--color-border-default)"}`}
                  >
                    <span
                      aria-hidden
                      className={`w-[6px] h-[6px] rounded-full ${t.enabled ? "bg-(--color-state-success-fg)" : "bg-(--color-text-faint)"}`}
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
              <div className="text-sm text-(--color-text-primary) leading-[1.55]">
                {t.kind === "schedule" && (
                  <div className="flex items-baseline gap-2 mb-[6px]">
                    <span className={sentenceTagCn}>WHEN</span>
                    <span className="flex-1 min-w-0">
                      {describeCron(t.schedule_cron, t.schedule_timezone)}
                      {t.schedule_start_at && (
                        <>
                          {" "}
                          <span className="text-(--color-text-muted) text-xs">
                            · starting {new Date(t.schedule_start_at).toLocaleString()}
                          </span>
                        </>
                      )}
                      {t.schedule_last_fired_at && (
                        <>
                          {" "}
                          <span className="text-(--color-text-faint) text-xs">
                            · last fired {formatRelative(t.schedule_last_fired_at)}
                          </span>
                        </>
                      )}
                    </span>
                  </div>
                )}
                <div className="flex items-baseline gap-2">
                  <span className={sentenceTagCn}>IF</span>
                  <span className="flex-1 min-w-0">{t.nl_description}</span>
                </div>
                {t.message && (
                  <div className="flex items-baseline gap-2 mt-[6px]">
                    <span className={sentenceTagCn}>THEN SEND</span>
                    <span className="flex-1 min-w-0">{t.message}</span>
                  </div>
                )}
                <div className="flex items-baseline gap-2 mt-[6px]">
                  <span className={sentenceTagCn}>TO</span>
                  <span className="flex-1 min-w-0 text-(--color-text-secondary)">
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
    <section className="mt-[28px] p-4 border border-(--color-border-default) rounded-(--radius-md) bg-(--color-bg-panel)">
      <div className="flex items-center justify-between mb-1">
        <h2 className="m-0 text-base">Slack channels</h2>
        {!adding && (
          <Button variant="action" size="sm" onClick={() => setAdding(true)}>
            + Add channel
          </Button>
        )}
      </div>
      <p className="mt-0 mb-3 text-[13px] text-(--color-text-muted)">
        Incoming webhooks you can point a trigger at. Create one in Slack (Apps → Incoming
        Webhooks), then pick it as a trigger&apos;s destination. Private to you.
      </p>

      {error && (
        <div className="text-(--color-state-danger-fg) text-[13px] mb-2">
          {error.message || "Failed to load channels."}
        </div>
      )}

      {adding && (
        <div className="flex flex-col gap-2 p-3 mb-3 border border-(--color-border-default) rounded-(--radius-sm) bg-(--color-bg-sunken)">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Channel name (e.g. PM Standup)"
            disabled={busy}
            maxLength={80}
            className="w-full py-2 px-[10px] box-border border border-(--color-border-default) rounded-(--radius-sm) text-sm"
          />
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://hooks.slack.com/services/…"
            disabled={busy}
            className="w-full py-2 px-[10px] box-border border border-(--color-border-default) rounded-(--radius-sm) text-sm"
          />
          {formError && (
            <div className="text-(--color-state-danger-fg) text-[13px]">{formError}</div>
          )}
          <div className="flex gap-2">
            <Button
              variant="action"
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
        <p className="text-(--color-text-muted) text-sm m-0">
          No channels yet — add one to deliver trigger fires to Slack.
        </p>
      )}

      {webhooks.length > 0 && (
        <ul className="list-none p-0 m-0">
          {webhooks.map((w) => (
            <li
              key={w.id}
              className="flex items-center gap-3 py-[10px] px-3 border border-(--color-border-default) rounded-(--radius-sm) mt-2 bg-(--color-bg-page)"
            >
              <div className="flex-1 min-w-0">
                <div className="font-medium text-sm text-(--color-text-primary)">
                  {w.name}
                </div>
                <div className="text-xs text-(--color-text-muted) mt-[2px] font-mono">
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


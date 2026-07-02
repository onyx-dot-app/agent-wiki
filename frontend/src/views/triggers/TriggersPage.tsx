"use client";

import { useState } from "react";

import { Button } from "@onyx-ai/opal/components";
import { SvgPlus, SvgWorkflow } from "@onyx-ai/opal/icons";
import { SettingsLayouts } from "@onyx-ai/opal/layouts";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { TriggerHistoryModal } from "@/components/triggers/TriggerHistoryModal";
import { TriggerModal } from "@/components/triggers/TriggerModal";
import { useRequireAuth } from "@/lib/auth";
import { describeCron } from "@/lib/cron";
import { formatScopePath } from "@/lib/format";
import {
  disconnectSlack,
  getSlackChannels,
  useSlackConnectStatus,
  type SlackChannel,
} from "@/lib/slackConnect";
import {
  createDestinationConfig,
  deleteDestinationConfig,
  deleteTrigger,
  getTriggerVersion,
  updateTrigger,
  useDestinationConfigs,
  useTriggers,
  type Trigger,
} from "@/lib/triggers";
import { ApiError } from "@/lib/api";

const sentenceTagCn =
  "shrink-0 text-[10px] font-semibold px-[6px] py-[1px] rounded-(--border-radius-04) bg-(--background-tint-03) text-(--text-05) uppercase tracking-[0.3px]";

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
  const { configs } = useDestinationConfigs();
  const destinationLabel = (t: Trigger) => {
    if (t.actions.length > 1) return `${t.actions.length} destinations`;
    const configId = t.actions[0]?.destination_config_id;
    if (!configId) return "Event log";
    const cfg = configs.find((c) => c.id === configId);
    return cfg ? `${cfg.type} · ${cfg.name}` : "(destination removed)";
  };
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Trigger | null>(null);
  const [historyFor, setHistoryFor] = useState<Trigger | null>(null);
  const confirmDialog = useConfirm();

  const listError = mutationError ?? listSwrError?.message ?? null;

  if (loading || !user) return <LoadingSpinner center />;

  async function onToggle(t: Trigger) {
    setBusyId(t.id);
    setMutationError(null);
    try {
      const updated = await updateTrigger(t.id, { enabled: !t.enabled });
      await refresh(
        (cur) => ({
          triggers: (cur?.triggers ?? []).map((x) =>
            x.id === t.id ? updated : x,
          ),
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
    if (
      !(await confirmDialog({
        title: "Delete this trigger?",
        body: `"${t.nl_description}"`,
        confirmLabel: "Delete",
      }))
    )
      return;
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
    <SettingsLayouts.Root width="lg">
      <SettingsLayouts.Header
        icon={SvgWorkflow}
        title="Triggers"
        description="Watch wiki pages for specific changes, or check on recurring schedules."
        divider
      />
      <SettingsLayouts.Body>
        <div className="mb-4 flex justify-end">
          <Button
            icon={SvgPlus}
            onClick={() => {
              setEditing(null);
              setModalOpen(true);
            }}
          >
            New trigger
          </Button>
        </div>
        {listError && (
          <div className="mb-3 rounded-(--border-radius-04) bg-(--status-error-01) p-[10px] text-[13px] text-(--status-text-error-05)">
            {listError}
          </div>
        )}

        {triggers.length === 0 && !listError && (
          <p className="text-sm text-(--text-03)">
            No triggers yet. Create one to start watching documents for changes.
          </p>
        )}

        <ul className="m-0 list-none p-0">
          {triggers.map((t) => (
            <li
              key={t.id}
              className={`mb-[10px] rounded-(--border-radius-08) border border-(--border-01) bg-(--background-tint-00) px-4 py-[14px] ${busyId === t.id ? "opacity-60" : "opacity-100"}`}
            >
              <div className="mb-[10px] flex flex-wrap items-center justify-between gap-3">
                <div className="flex min-w-0 flex-wrap items-baseline gap-[10px] font-mono text-xs text-(--text-03)">
                  <span title={t.scope_path}>
                    {formatScopePath(t.scope_path)}
                  </span>
                  <span className="text-[11px] text-(--text-02)">{t.id}</span>
                  {t.last_edited_at && (
                    <span
                      title={new Date(t.last_edited_at).toLocaleString()}
                      className="text-[11px] text-(--text-02)"
                    >
                      edited {formatRelative(t.last_edited_at)}
                    </span>
                  )}
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                  <span
                    className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-[2px] text-[11px] font-semibold tracking-[0.3px] ${t.enabled ? "border-(--border-01) bg-(--background-tint-03) text-(--text-05)" : "border-(--border-01) bg-(--background-tint-02) text-(--text-03)"}`}
                  >
                    <span
                      aria-hidden
                      className={`h-[6px] w-[6px] rounded-full ${t.enabled ? "bg-(--status-text-success-05)" : "bg-(--text-02)"}`}
                    />
                    {t.enabled ? "ENABLED" : "DISABLED"}
                  </span>
                  <Button
                    size="sm"
                    onClick={() => onToggle(t)}
                    disabled={busyId === t.id}
                  >
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
              <div className="text-sm leading-[1.55] text-(--text-05)">
                {t.kind === "schedule" && (
                  <div className="mb-[6px] flex items-baseline gap-2">
                    <span className={sentenceTagCn}>WHEN</span>
                    <span className="min-w-0 flex-1">
                      {describeCron(t.schedule_cron, t.schedule_timezone)}
                      {t.schedule_start_at && (
                        <>
                          {" "}
                          <span className="text-xs text-(--text-03)">
                            · starting{" "}
                            {new Date(t.schedule_start_at).toLocaleString()}
                          </span>
                        </>
                      )}
                      {t.schedule_last_fired_at && (
                        <>
                          {" "}
                          <span className="text-xs text-(--text-02)">
                            · last checked{" "}
                            {formatRelative(t.schedule_last_fired_at)}
                          </span>
                        </>
                      )}
                    </span>
                  </div>
                )}
                <div className="flex items-baseline gap-2">
                  <span className={sentenceTagCn}>IF</span>
                  <span className="min-w-0 flex-1">{t.nl_description}</span>
                </div>
                {t.actions
                  .filter((a) => a.message)
                  .map((a, i) => (
                    <div
                      key={`${a.destination_config_id ?? "event_log"}-${a.message}-${i}`}
                      className="mt-[6px] flex items-baseline gap-2"
                    >
                      <span className={sentenceTagCn}>THEN SEND</span>
                      <span className="min-w-0 flex-1">{a.message}</span>
                    </div>
                  ))}
                <div className="mt-[6px] flex items-baseline gap-2">
                  <span className={sentenceTagCn}>TO</span>
                  <span className="min-w-0 flex-1 text-(--text-04)">
                    {destinationLabel(t)}
                  </span>
                </div>
              </div>
            </li>
          ))}
        </ul>

        <DestinationsCard />
      </SettingsLayouts.Body>

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
              actions: version.actions,
              enabled: version.enabled,
              kind: version.kind ?? historyFor.kind,
              schedule_cron: version.schedule_cron,
              schedule_timezone: version.schedule_timezone,
              schedule_start_at: version.schedule_start_at,
            });
            setHistoryFor(null);
            setModalOpen(true);
          } catch (e) {
            setMutationError(
              e instanceof Error ? e.message : "failed to load version",
            );
          }
        }}
      />
    </SettingsLayouts.Root>
  );
}

function DestinationsCard() {
  const { configs, error, isLoading, refresh } = useDestinationConfigs();
  const { status: slack, refresh: refreshSlack } = useSlackConnectStatus();
  const [mode, setMode] = useState<"closed" | "channel" | "webhook">("closed");
  const [channels, setChannels] = useState<SlackChannel[] | null>(null);
  const [channelId, setChannelId] = useState("");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const confirmDialog = useConfirm();

  async function openChannelPicker() {
    setMode("channel");
    setFormError(null);
    if (channels === null) {
      try {
        setChannels(await getSlackChannels());
      } catch (e) {
        setFormError(
          e instanceof ApiError ? e.message : "failed to load channels",
        );
      }
    }
  }

  async function addConfig(
    input: Parameters<typeof createDestinationConfig>[0],
  ) {
    setBusy(true);
    setFormError(null);
    try {
      await createDestinationConfig(input);
      await refresh();
      setMode("closed");
      setChannelId("");
      setName("");
      setUrl("");
    } catch (e) {
      setFormError(
        e instanceof ApiError ? e.message : "failed to add destination",
      );
    } finally {
      setBusy(false);
    }
  }

  function onAddChannel() {
    const ch = (channels ?? []).find((c) => c.id === channelId);
    if (!ch) return;
    void addConfig({
      type: "slack",
      name: `#${ch.name}`,
      config: { channel_id: ch.id, channel_name: ch.name },
    });
  }

  function onAddDm() {
    void addConfig({ type: "slack", name: "DM me", config: { dm: true } });
  }

  function onAddWebhook() {
    if (!name.trim() || !url.trim()) return;
    void addConfig({ type: "slack", name: name.trim(), secret: url.trim() });
  }

  async function onDisconnect() {
    if (
      !(await confirmDialog({
        title: "Disconnect Slack?",
        body: "Channel and DM destinations will stop delivering until you reconnect.",
        confirmLabel: "Disconnect",
      }))
    )
      return;
    await disconnectSlack();
    await refreshSlack();
  }

  async function onDelete(id: string, label: string) {
    if (
      !(await confirmDialog({
        title: `Delete "${label}"?`,
        body: "Triggers pointing at it will stop delivering there.",
        confirmLabel: "Delete",
      }))
    )
      return;
    try {
      await deleteDestinationConfig(id);
      await refresh();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "failed to delete");
    }
  }

  const connected = Boolean(slack?.connected);

  return (
    <section className="mt-[28px] rounded-(--border-radius-08) border border-(--border-01) bg-(--background-tint-01) p-4">
      <div className="mb-1 flex items-center justify-between">
        <h2 className="m-0 text-base">Destinations</h2>
        <div className="flex gap-2">
          {mode === "closed" && connected && (
            <>
              <Button size="sm" onClick={() => void openChannelPicker()}>
                + Channel
              </Button>
              <Button size="sm" onClick={() => void onAddDm()} disabled={busy}>
                + DM me
              </Button>
            </>
          )}
          {mode === "closed" && (
            <Button size="sm" onClick={() => setMode("webhook")}>
              + Webhook
            </Button>
          )}
        </div>
      </div>
      <p className="mt-0 mb-3 text-[13px] text-(--text-03)">
        Delivery targets you can point a trigger at. Private to you.
      </p>

      {slack?.configured && (
        <div className="mb-3 flex items-center justify-between rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-02) px-[14px] py-3">
          {connected ? (
            <>
              <div className="text-[13px]">
                Connected to <strong>{slack?.team_name ?? "Slack"}</strong>
              </div>
              <Button
                size="sm"
                variant="danger"
                onClick={() => void onDisconnect()}
              >
                Disconnect
              </Button>
            </>
          ) : (
            <>
              <div className="text-[13px]">
                Connect Slack to deliver to channels or DMs as the Agent Wiki
                bot.
              </div>
              <Button
                size="sm"
                variant="action"
                onClick={() => {
                  if (slack?.connect_url)
                    window.location.href = `${slack.connect_url}?return_to=/app/triggers`;
                }}
              >
                Connect Slack
              </Button>
            </>
          )}
        </div>
      )}

      {error && (
        <div className="mb-2 text-[13px] text-(--status-text-error-05)">
          {error.message || "Failed to load destinations."}
        </div>
      )}

      {mode === "channel" && (
        <div className="mb-3 flex flex-col gap-2 rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-02) p-3">
          <select
            value={channelId}
            onChange={(e) => setChannelId(e.target.value)}
            disabled={busy || channels === null}
            className="box-border w-full rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm"
          >
            <option value="">
              {channels === null ? "Loading channels…" : "Pick a channel"}
            </option>
            {(channels ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                #{c.name}
                {c.is_private ? " (private)" : ""}
              </option>
            ))}
          </select>
          {formError && (
            <div className="text-[13px] text-(--status-text-error-05)">
              {formError}
            </div>
          )}
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="action"
              disabled={busy || !channelId}
              onClick={onAddChannel}
            >
              {busy ? "Adding…" : "Add channel"}
            </Button>
            <Button size="sm" disabled={busy} onClick={() => setMode("closed")}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {mode === "webhook" && (
        <div className="mb-3 flex flex-col gap-2 rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-02) p-3">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name (e.g. PM Standup)"
            disabled={busy}
            maxLength={80}
            className="box-border w-full rounded-(--border-radius-04) border border-(--border-01) px-[10px] py-2 text-sm"
          />
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://hooks.slack.com/services/…"
            disabled={busy}
            className="box-border w-full rounded-(--border-radius-04) border border-(--border-01) px-[10px] py-2 text-sm"
          />
          {formError && (
            <div className="text-[13px] text-(--status-text-error-05)">
              {formError}
            </div>
          )}
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="action"
              disabled={busy || !name.trim() || !url.trim()}
              onClick={onAddWebhook}
            >
              {busy ? "Adding…" : "Add"}
            </Button>
            <Button size="sm" disabled={busy} onClick={() => setMode("closed")}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {formError && mode === "closed" && (
        <div className="mb-2 text-[13px] text-(--status-text-error-05)">
          {formError}
        </div>
      )}

      {isLoading && configs.length === 0 && !error && <LoadingSpinner />}

      {!isLoading && configs.length === 0 && mode === "closed" && (
        <p className="m-0 text-sm text-(--text-03)">
          No destinations yet &mdash; add one to deliver trigger fires to Slack.
        </p>
      )}

      {configs.length > 0 && (
        <ul className="m-0 list-none p-0">
          {configs.map((c) => (
            <li
              key={c.id}
              className="mt-2 flex items-center gap-3 rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-3 py-[10px]"
            >
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-(--text-05)">
                  {c.name}
                </div>
                <div className="mt-[2px] font-mono text-xs text-(--text-03)">
                  {c.type}
                  {c.has_secret ? " · webhook" : " · bot"}
                </div>
              </div>
              <Button
                size="sm"
                variant="danger"
                onClick={() => void onDelete(c.id, c.name)}
              >
                Delete
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

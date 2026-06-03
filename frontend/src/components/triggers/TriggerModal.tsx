"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";

import { Button } from "@onyx-ai/opal/components";
import {
  PRESET_OPTIONS,
  WEEKDAY_NAMES,
  browserTimezone,
  cronToParts,
  defaultScheduleParts,
  describeCron,
  listTimezones,
  localInputToUtcIso,
  partsToCron,
  utcIsoToLocalInput,
  type FrequencyPreset,
} from "@/lib/cron";
import {
  createSlackWebhook,
  createTrigger,
  getTriggerDestinations,
  updateTrigger,
  useSlackWebhooks,
  type Trigger,
  type TriggerCreateInput,
  type TriggerDestination,
  type TriggerKind,
} from "@/lib/triggers";

interface Props {
  open: boolean;
  initial?: Partial<Trigger>;
  onClose: () => void;
  onSaved: (t: Trigger) => void;
  /** Lock the scope_path input so callers (e.g. doc page) can pin it. */
  lockScope?: boolean;
}

const FALLBACK_DESTINATIONS: TriggerDestination[] = [
  { id: "event_log", name: "Event Log", description: "Tracked in the event log only." },
];

const EXAMPLE_SCOPE = "projects/release-v3.md";
const EXAMPLE_IF = "the document is updated with a release version";
const EXAMPLE_SEND =
  "a message saying that the version has been finalized or updated to the specific version number.";

export function TriggerModal({ open, initial, onClose, onSaved, lockScope }: Props) {
  const isEdit = Boolean(initial?.id);
  const [scopePath, setScopePath] = useState("");
  const [ifText, setIfText] = useState("");
  const [sendText, setSendText] = useState("");
  const [destinations, setDestinations] = useState<TriggerDestination[]>(
    FALLBACK_DESTINATIONS,
  );
  const [destination, setDestination] = useState(FALLBACK_DESTINATIONS[0].id);
  const { webhooks: slackWebhooks, refresh: refreshSlackWebhooks } = useSlackWebhooks();
  const [slackWebhookId, setSlackWebhookId] = useState<string | null>(null);
  const [addingChannel, setAddingChannel] = useState(false);
  const [newChannelName, setNewChannelName] = useState("");
  const [newChannelUrl, setNewChannelUrl] = useState("");
  const [kind, setKind] = useState<TriggerKind>("delta");
  const [scheduleParts, setScheduleParts] = useState(defaultScheduleParts());
  const [customCron, setCustomCron] = useState("");
  const [tz, setTz] = useState(browserTimezone());
  const [startAtLocal, setStartAtLocal] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tzOptions = useMemo(() => listTimezones(), []);

  useEffect(() => {
    if (!open) return;
    setScopePath(initial?.scope_path ?? "");
    setIfText(initial?.nl_description ?? "");
    setSendText(initial?.message ?? "");
    setDestination(initial?.destination ?? FALLBACK_DESTINATIONS[0].id);
    setSlackWebhookId(initial?.slack_webhook_id ?? null);
    setAddingChannel(false);
    setNewChannelName("");
    setNewChannelUrl("");
    setKind((initial?.kind as TriggerKind) ?? "delta");
    const parts = cronToParts(initial?.schedule_cron ?? null);
    setScheduleParts(parts);
    setCustomCron(parts.preset === "custom" ? (initial?.schedule_cron ?? "") : "");
    setTz(initial?.schedule_timezone ?? browserTimezone());
    setStartAtLocal(utcIsoToLocalInput(initial?.schedule_start_at ?? null));
    setError(null);
  }, [
    open,
    initial?.id,
    initial?.scope_path,
    initial?.nl_description,
    initial?.message,
    initial?.destination,
    initial?.slack_webhook_id,
    initial?.kind,
    initial?.schedule_cron,
    initial?.schedule_timezone,
    initial?.schedule_start_at,
  ]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    getTriggerDestinations()
      .then((rows) => {
        if (cancelled || rows.length === 0) return;
        setDestinations(rows);
        setDestination((cur) => (rows.some((r) => r.id === cur) ? cur : rows[0].id));
      })
      .catch(() => {
        // Keep fallback list silently.
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) return null;

  const computedCron = partsToCron(scheduleParts, customCron);
  const cronSummary = describeCron(computedCron || null, tz);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const nl = ifText.trim();
      const msg = sendText.trim();
      const baseInput: TriggerCreateInput = {
        scope_path: scopePath.trim(),
        nl_description: nl,
        message: msg,
        destination,
        slack_webhook_id: destination === "slack" ? slackWebhookId : null,
        kind,
      };
      if (kind === "schedule") {
        if (!computedCron) {
          throw new Error("schedule_cron is required");
        }
        baseInput.schedule_cron = computedCron;
        baseInput.schedule_timezone = tz;
        baseInput.schedule_start_at = localInputToUtcIso(startAtLocal);
      }
      let saved: Trigger;
      if (isEdit && initial?.id) {
        // ``kind`` is immutable on update — don't include it. Schedule
        // fields are sent only when this is a schedule trigger; for
        // delta we explicitly null them so a kind-flip in the DB stays
        // consistent (the API already enforces the invariant).
        saved = await updateTrigger(initial.id, {
          scope_path: scopePath.trim(),
          nl_description: nl,
          message: msg,
          destination,
          slack_webhook_id: destination === "slack" ? slackWebhookId : null,
          schedule_cron: kind === "schedule" ? computedCron : null,
          schedule_timezone: kind === "schedule" ? tz : null,
          schedule_start_at: kind === "schedule" ? localInputToUtcIso(startAtLocal) : null,
        });
      } else {
        saved = await createTrigger(baseInput);
      }
      onSaved(saved);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  }

  const canSave =
    scopePath.trim() &&
    ifText.trim() &&
    sendText.trim() &&
    (kind === "delta" || (computedCron && tz)) &&
    (destination !== "slack" || Boolean(slackWebhookId));
  const selectedDest = destinations.find((d) => d.id === destination);
  const destDescription =
    destination === "slack" ? "" : selectedDest?.description ?? "";

  // The "TO" select value encodes a slack channel as ``slack:<id>`` so one
  // control can pick Event Log or any of the user's channels.
  const destSelectValue =
    destination === "slack" && slackWebhookId ? `slack:${slackWebhookId}` : destination;

  function onPickDestination(value: string) {
    if (value.startsWith("slack:")) {
      setDestination("slack");
      setSlackWebhookId(value.slice("slack:".length));
    } else {
      setDestination(value);
      setSlackWebhookId(null);
    }
  }

  async function onAddChannel() {
    const name = newChannelName.trim();
    const url = newChannelUrl.trim();
    if (!name || !url) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createSlackWebhook(name, url);
      await refreshSlackWebhooks();
      setDestination("slack");
      setSlackWebhookId(created.id);
      setAddingChannel(false);
      setNewChannelName("");
      setNewChannelUrl("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to add channel");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      className="fixed inset-0 bg-(--color-overlay) flex items-center justify-center z-[100]"
    >
      <div
        aria-hidden
        className="absolute top-1/2 left-1/2 translate-x-[calc(-50%+40px)] translate-y-[calc(-50%+44px)] rotate-[-1.5deg] w-[min(560px,92vw)] blur-[3.5px] opacity-85 pointer-events-none z-0"
      >
        <PreviewCard
          scope={EXAMPLE_SCOPE}
          ifText={EXAMPLE_IF}
          sendText={EXAMPLE_SEND}
          destLabel="Event log only"
        />
      </div>

      <form
        onSubmit={onSubmit}
        className="relative bg-(--color-bg-page) rounded-(--radius-lg) max-h-[92vh] overflow-y-auto p-6 shadow-(--shadow-modal) flex flex-col gap-4 z-[1] w-[min(560px,92vw)]"
      >
        <div>
          <h2 className="m-0 text-lg font-semibold text-(--color-text-primary)">
            {isEdit ? "Edit trigger" : "Create a trigger"}
          </h2>
          <p className="mt-[6px] mb-0 text-[13px] text-(--color-text-secondary) leading-[1.55]">
            Triggers monitor documents or folders and send events when a
            specified condition is met. They can fire on document updates
            or on a recurring schedule.
          </p>
        </div>

        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">When to run</span>
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as TriggerKind)}
            disabled={busy || isEdit}
            className={`py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page) ${isEdit ? "cursor-not-allowed" : "cursor-pointer"}`}
          >
            <option value="delta">On a document update</option>
            <option value="schedule">On a schedule</option>
          </select>
          {isEdit && (
            <span className="text-xs text-(--color-text-muted) leading-[1.4]">
              The trigger type can&rsquo;t be changed after creation. Delete
              and recreate to switch.
            </span>
          )}
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">Watching</span>
          <input
            value={scopePath}
            onChange={(e) => setScopePath(e.target.value)}
            disabled={busy || lockScope}
            placeholder="projects/foo.md or projects"
            className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page)"
          />
          <span className="text-xs text-(--color-text-muted) leading-[1.4]">
            e.g. <code>projects/foo.md</code> for one document,{" "}
            <code>projects</code> for a folder, or <code>/</code> to watch
            the whole wiki.
          </span>
        </label>

        {kind === "schedule" && (
          <ScheduleFields
            parts={scheduleParts}
            onPartsChange={setScheduleParts}
            customCron={customCron}
            onCustomCronChange={setCustomCron}
            tz={tz}
            onTzChange={setTz}
            tzOptions={tzOptions}
            startAtLocal={startAtLocal}
            onStartAtChange={setStartAtLocal}
            cronSummary={cronSummary}
            computedCron={computedCron}
            disabled={busy}
          />
        )}

        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">If</span>
          <textarea
            value={ifText}
            onChange={(e) => setIfText(e.target.value)}
            disabled={busy}
            placeholder={EXAMPLE_IF}
            rows={2}
            className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page) resize-y"
          />
          {kind === "schedule" && (
            <span className="text-xs text-(--color-text-muted) leading-[1.4]">
              On each scheduled run, the trigger fires only when this
              condition is satisfied by the current state of the
              documents under <em>Watching</em>.
            </span>
          )}
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">Then send</span>
          <textarea
            value={sendText}
            onChange={(e) => setSendText(e.target.value)}
            disabled={busy}
            placeholder={EXAMPLE_SEND}
            rows={2}
            className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page) resize-y"
          />
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">To</span>
          <select
            value={destSelectValue}
            onChange={(e) => onPickDestination(e.target.value)}
            disabled={busy}
            className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page) cursor-pointer"
          >
            {destinations
              .filter((d) => d.id !== "slack")
              .map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            {slackWebhooks.map((w) => (
              <option key={w.id} value={`slack:${w.id}`}>
                Slack · {w.name}
              </option>
            ))}
          </select>
          {destDescription && <span className="text-xs text-(--color-text-muted) leading-[1.4]">{destDescription}</span>}
          {destination === "slack" && !slackWebhookId && (
            <span className="text-xs text-(--color-state-danger-fg) leading-[1.4]">
              Pick a Slack channel, or add one below.
            </span>
          )}
          {!addingChannel ? (
            <button
              type="button"
              onClick={() => setAddingChannel(true)}
              disabled={busy}
              className="self-start mt-[6px] bg-transparent border-none p-0 cursor-pointer text-[13px] text-(--color-accent-fg)"
            >
              + Add Slack channel
            </button>
          ) : (
            <div className="mt-2 p-[10px] border border-(--color-border-default) rounded-(--radius-sm) bg-(--color-bg-sunken) flex flex-col gap-2">
              <input
                value={newChannelName}
                onChange={(e) => setNewChannelName(e.target.value)}
                placeholder="Channel name (e.g. PM Standup)"
                disabled={busy}
                className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page)"
              />
              <input
                value={newChannelUrl}
                onChange={(e) => setNewChannelUrl(e.target.value)}
                placeholder="https://hooks.slack.com/services/…"
                disabled={busy}
                className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page)"
              />
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="action"
                  size="sm"
                  disabled={busy || !newChannelName.trim() || !newChannelUrl.trim()}
                  onClick={() => void onAddChannel()}
                >
                  Add channel
                </Button>
                <Button
                  type="button"
                  size="sm"
                  disabled={busy}
                  onClick={() => setAddingChannel(false)}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </label>

        {error && (
          <div className="bg-(--color-state-danger-bg) text-(--color-state-danger-fg) rounded-(--radius-sm) p-[10px] text-[13px]">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button type="button" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" variant="action" disabled={busy || !canSave}>
            {busy ? "Saving…" : isEdit ? "Save" : "Create"}
          </Button>
        </div>
      </form>
    </div>
  );
}

interface ScheduleFieldsProps {
  parts: ReturnType<typeof defaultScheduleParts>;
  onPartsChange: (p: ReturnType<typeof defaultScheduleParts>) => void;
  customCron: string;
  onCustomCronChange: (s: string) => void;
  tz: string;
  onTzChange: (s: string) => void;
  tzOptions: string[];
  startAtLocal: string;
  onStartAtChange: (s: string) => void;
  cronSummary: string;
  computedCron: string;
  disabled?: boolean;
}

function ScheduleFields({
  parts,
  onPartsChange,
  customCron,
  onCustomCronChange,
  tz,
  onTzChange,
  tzOptions,
  startAtLocal,
  onStartAtChange,
  cronSummary,
  computedCron,
  disabled,
}: ScheduleFieldsProps) {
  const showTimeOfDay = parts.preset === "daily" || parts.preset === "weekly" || parts.preset === "monthly";
  const showWeekday = parts.preset === "weekly";
  const showDayOfMonth = parts.preset === "monthly";
  const isCustom = parts.preset === "custom";

  const timeValue = `${pad(parts.hour)}:${pad(parts.minute)}`;

  return (
    <div className="border border-(--color-border-default) rounded-(--radius-sm) p-[14px] flex flex-col gap-3 bg-(--color-bg-panel)">
      <label className="flex flex-col gap-1.5">
        <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">Frequency</span>
        <select
          value={parts.preset}
          onChange={(e) => onPartsChange({ ...parts, preset: e.target.value as FrequencyPreset })}
          disabled={disabled}
          className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page) cursor-pointer"
        >
          {PRESET_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      {showTimeOfDay && (
        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">Time of day</span>
          <input
            type="time"
            value={timeValue}
            onChange={(e) => {
              const [h, m] = e.target.value.split(":").map(Number);
              if (Number.isFinite(h) && Number.isFinite(m)) {
                onPartsChange({ ...parts, hour: h, minute: m });
              }
            }}
            disabled={disabled}
            className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page)"
          />
          <span className="text-xs text-(--color-text-muted) leading-[1.4]">Interpreted in the timezone selected below.</span>
        </label>
      )}

      {showWeekday && (
        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">Day of week</span>
          <select
            value={parts.dayOfWeek}
            onChange={(e) => onPartsChange({ ...parts, dayOfWeek: Number(e.target.value) })}
            disabled={disabled}
            className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page) cursor-pointer"
          >
            {WEEKDAY_NAMES.map((name, i) => (
              <option key={i} value={i}>
                {name}
              </option>
            ))}
          </select>
        </label>
      )}

      {showDayOfMonth && (
        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">Day of month</span>
          <input
            type="number"
            min={1}
            max={31}
            value={parts.dayOfMonth}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (Number.isFinite(n) && n >= 1 && n <= 31) {
                onPartsChange({ ...parts, dayOfMonth: n });
              }
            }}
            disabled={disabled}
            className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page)"
          />
          <span className="text-xs text-(--color-text-muted) leading-[1.4]">
            Months without this day (e.g. day 31 in February) skip that
            month entirely &mdash; the schedule does not roll over to the
            next valid day.
          </span>
        </label>
      )}

      <label className="flex flex-col gap-1.5">
        <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">Timezone</span>
        <select
          value={tz}
          onChange={(e) => onTzChange(e.target.value)}
          disabled={disabled}
          className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page) cursor-pointer"
        >
          {tzOptions.includes(tz) ? null : <option value={tz}>{tz}</option>}
          {tzOptions.map((zone) => (
            <option key={zone} value={zone}>
              {zone}
            </option>
          ))}
        </select>
        <span className="text-xs text-(--color-text-muted) leading-[1.4]">
          The schedule runs in this timezone. Daylight-saving
          transitions are handled automatically.
        </span>
      </label>

      <label className="flex flex-col gap-1.5">
        <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">Do not fire before (optional)</span>
        <input
          type="datetime-local"
          value={startAtLocal}
          onChange={(e) => onStartAtChange(e.target.value)}
          disabled={disabled}
          className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page)"
        />
        <span className="text-xs text-(--color-text-muted) leading-[1.4]">
          Anchored to your local time. Leave empty to start at the next
          scheduled run. Useful for delaying a launch (e.g.
          &ldquo;don&rsquo;t start until next Monday&rdquo;).
        </span>
      </label>

      <details>
        <summary className="cursor-pointer text-xs font-semibold text-(--color-text-secondary) select-none">
          Advanced &mdash; raw cron expression
        </summary>
        <div className="flex flex-col gap-2 mt-[10px]">
          <div className="grid grid-cols-5 gap-1.5">
            {CRON_FIELD_HELP.map((f) => (
              <div key={f.label}>
                <div className="text-[10px] font-bold text-(--color-text-muted) uppercase tracking-[0.06px]">
                  {f.label}
                </div>
                <div className="text-[11px] text-(--color-text-faint) leading-[1.4]">
                  {f.help}
                </div>
              </div>
            ))}
          </div>
          <input
            value={isCustom ? customCron : computedCron}
            onChange={(e) => {
              onCustomCronChange(e.target.value);
              onPartsChange({ ...parts, preset: "custom" });
            }}
            disabled={disabled}
            placeholder="*/15 * * * *"
            className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page) font-mono"
          />
          <span className="text-xs text-(--color-text-muted) leading-[1.4]">
            Standard 5-field cron. Editing this switches the frequency to
            &ldquo;Custom&rdquo;.
          </span>
        </div>
      </details>

      <div className="text-[13px] text-(--color-text-primary) bg-(--color-bg-page) border border-(--color-border-subtle) rounded-(--radius-xs) p-2 leading-[1.5]">
        <strong>{cronSummary}</strong>
      </div>
    </div>
  );
}

const CRON_FIELD_HELP: { label: string; help: string }[] = [
  { label: "Minute", help: "0–59" },
  { label: "Hour", help: "0–23" },
  { label: "Day of month", help: "1–31, * for any" },
  { label: "Month", help: "1–12" },
  { label: "Day of week", help: "0–6, Sun=0" },
];

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

function PreviewCard({
  scope,
  ifText,
  sendText,
  destLabel,
}: {
  scope: string;
  ifText: string;
  sendText: string;
  destLabel: string;
}) {
  return (
    <div className="bg-(--color-bg-page) rounded-(--radius-lg) p-6 shadow-(--shadow-modal) flex flex-col gap-4">
      <div>
        <h2 className="m-0 text-lg font-semibold text-(--color-text-primary)">Create a trigger</h2>
        <p className="mt-[6px] mb-0 text-[13px] text-(--color-text-secondary)">
          Triggers monitor documents or folders and send events when a specified change occurs.
        </p>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">Watching</span>
        <div className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page) text-(--color-text-primary)">{scope}</div>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">If</span>
        <div className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page) text-(--color-text-primary) whitespace-pre-wrap">{ifText}</div>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">Then send</span>
        <div className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page) text-(--color-text-primary) whitespace-pre-wrap">{sendText}</div>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="text-[11px] font-bold text-(--color-text-muted) uppercase tracking-[0.06em]">To</span>
        <div className="py-2 px-[10px] border border-(--color-border-default) rounded-(--radius-sm) text-sm outline-none w-full box-border bg-(--color-bg-page) text-(--color-text-primary)">{destLabel}</div>
      </div>
    </div>
  );
}


"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";

import { Button } from "@/components/common/Button";
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
import { color, radius, shadow } from "@/lib/theme";

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
      style={{
        position: "fixed",
        inset: 0,
        background: color.overlay,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <div
        aria-hidden
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%) translate(40px, 44px) rotate(-1.5deg)",
          width: "min(560px, 92vw)",
          filter: "blur(3.5px)",
          opacity: 0.85,
          pointerEvents: "none",
          zIndex: 0,
        }}
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
        style={{
          position: "relative",
          background: color.bg.page,
          borderRadius: radius.lg,
          width: "min(560px, 92vw)",
          maxHeight: "92vh",
          overflowY: "auto",
          padding: 24,
          boxShadow: shadow.modal,
          display: "flex",
          flexDirection: "column",
          gap: 16,
          zIndex: 1,
        }}
      >
        <div>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: color.text.primary }}>
            {isEdit ? "Edit trigger" : "Create a trigger"}
          </h2>
          <p style={{ margin: "6px 0 0", fontSize: 13, color: color.text.secondary, lineHeight: 1.55 }}>
            Triggers monitor documents or folders and send events when a
            specified condition is met. They can fire on document updates
            or on a recurring schedule.
          </p>
        </div>

        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>When to run</span>
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as TriggerKind)}
            disabled={busy || isEdit}
            style={{ ...inputStyle, cursor: isEdit ? "not-allowed" : "pointer" }}
          >
            <option value="delta">On a document update</option>
            <option value="schedule">On a schedule</option>
          </select>
          {isEdit && (
            <span style={fieldHintStyle}>
              The trigger type can&rsquo;t be changed after creation. Delete
              and recreate to switch.
            </span>
          )}
        </label>

        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>Watching</span>
          <input
            value={scopePath}
            onChange={(e) => setScopePath(e.target.value)}
            disabled={busy || lockScope}
            placeholder="projects/foo.md or projects"
            style={inputStyle}
          />
          <span style={fieldHintStyle}>
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

        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>If</span>
          <textarea
            value={ifText}
            onChange={(e) => setIfText(e.target.value)}
            disabled={busy}
            placeholder={EXAMPLE_IF}
            rows={2}
            style={{ ...inputStyle, fontFamily: "inherit", resize: "vertical" }}
          />
          {kind === "schedule" && (
            <span style={fieldHintStyle}>
              On each scheduled run, the trigger fires only when this
              condition is satisfied by the current state of the
              documents under <em>Watching</em>.
            </span>
          )}
        </label>

        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>Then send</span>
          <textarea
            value={sendText}
            onChange={(e) => setSendText(e.target.value)}
            disabled={busy}
            placeholder={EXAMPLE_SEND}
            rows={2}
            style={{ ...inputStyle, fontFamily: "inherit", resize: "vertical" }}
          />
        </label>

        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>To</span>
          <select
            value={destSelectValue}
            onChange={(e) => onPickDestination(e.target.value)}
            disabled={busy}
            style={{ ...inputStyle, cursor: "pointer" }}
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
          {destDescription && <span style={fieldHintStyle}>{destDescription}</span>}
          {destination === "slack" && !slackWebhookId && (
            <span style={{ ...fieldHintStyle, color: color.state.danger.fg }}>
              Pick a Slack channel, or add one below.
            </span>
          )}
          {!addingChannel ? (
            <button
              type="button"
              onClick={() => setAddingChannel(true)}
              disabled={busy}
              style={{
                alignSelf: "flex-start",
                marginTop: 6,
                background: "none",
                border: "none",
                padding: 0,
                cursor: "pointer",
                fontSize: 13,
                color: color.accent.fg,
              }}
            >
              + Add Slack channel
            </button>
          ) : (
            <div
              style={{
                marginTop: 8,
                padding: 10,
                border: `1px solid ${color.border.default}`,
                borderRadius: radius.sm,
                background: color.bg.sunken,
                display: "flex",
                flexDirection: "column",
                gap: 8,
              }}
            >
              <input
                value={newChannelName}
                onChange={(e) => setNewChannelName(e.target.value)}
                placeholder="Channel name (e.g. PM Standup)"
                disabled={busy}
                style={inputStyle}
              />
              <input
                value={newChannelUrl}
                onChange={(e) => setNewChannelUrl(e.target.value)}
                placeholder="https://hooks.slack.com/services/…"
                disabled={busy}
                style={inputStyle}
              />
              <div style={{ display: "flex", gap: 8 }}>
                <Button
                  type="button"
                  variant="primary"
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
          <div
            style={{
              background: color.state.danger.bg,
              color: color.state.danger.fg,
              borderRadius: radius.sm,
              padding: 10,
              fontSize: 13,
            }}
          >
            {error}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <Button type="button" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={busy || !canSave}>
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
    <div
      style={{
        border: `1px solid ${color.border.default}`,
        borderRadius: radius.sm,
        padding: 14,
        display: "flex",
        flexDirection: "column",
        gap: 12,
        background: color.bg.panel,
      }}
    >
      <label style={fieldStyle}>
        <span style={fieldLabelStyle}>Frequency</span>
        <select
          value={parts.preset}
          onChange={(e) => onPartsChange({ ...parts, preset: e.target.value as FrequencyPreset })}
          disabled={disabled}
          style={{ ...inputStyle, cursor: "pointer" }}
        >
          {PRESET_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      {showTimeOfDay && (
        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>Time of day</span>
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
            style={inputStyle}
          />
          <span style={fieldHintStyle}>Interpreted in the timezone selected below.</span>
        </label>
      )}

      {showWeekday && (
        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>Day of week</span>
          <select
            value={parts.dayOfWeek}
            onChange={(e) => onPartsChange({ ...parts, dayOfWeek: Number(e.target.value) })}
            disabled={disabled}
            style={{ ...inputStyle, cursor: "pointer" }}
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
        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>Day of month</span>
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
            style={inputStyle}
          />
          <span style={fieldHintStyle}>
            Months without this day (e.g. day 31 in February) skip that
            month entirely &mdash; the schedule does not roll over to the
            next valid day.
          </span>
        </label>
      )}

      <label style={fieldStyle}>
        <span style={fieldLabelStyle}>Timezone</span>
        <select
          value={tz}
          onChange={(e) => onTzChange(e.target.value)}
          disabled={disabled}
          style={{ ...inputStyle, cursor: "pointer" }}
        >
          {tzOptions.includes(tz) ? null : <option value={tz}>{tz}</option>}
          {tzOptions.map((zone) => (
            <option key={zone} value={zone}>
              {zone}
            </option>
          ))}
        </select>
        <span style={fieldHintStyle}>
          The schedule runs in this timezone. Daylight-saving
          transitions are handled automatically.
        </span>
      </label>

      <label style={fieldStyle}>
        <span style={fieldLabelStyle}>Do not fire before (optional)</span>
        <input
          type="datetime-local"
          value={startAtLocal}
          onChange={(e) => onStartAtChange(e.target.value)}
          disabled={disabled}
          style={inputStyle}
        />
        <span style={fieldHintStyle}>
          Anchored to your local time. Leave empty to start at the next
          scheduled run. Useful for delaying a launch (e.g.
          &ldquo;don&rsquo;t start until next Monday&rdquo;).
        </span>
      </label>

      <details>
        <summary
          style={{
            cursor: "pointer",
            fontSize: 12,
            fontWeight: 600,
            color: color.text.secondary,
            userSelect: "none",
          }}
        >
          Advanced &mdash; raw cron expression
        </summary>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            marginTop: 10,
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(5, 1fr)",
              gap: 6,
            }}
          >
            {CRON_FIELD_HELP.map((f) => (
              <div key={f.label}>
                <div
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    color: color.text.muted,
                    textTransform: "uppercase",
                    letterSpacing: 0.06,
                  }}
                >
                  {f.label}
                </div>
                <div style={{ fontSize: 11, color: color.text.faint, lineHeight: 1.4 }}>
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
            style={{ ...inputStyle, fontFamily: "ui-monospace, Menlo, monospace" }}
          />
          <span style={fieldHintStyle}>
            Standard 5-field cron. Editing this switches the frequency to
            &ldquo;Custom&rdquo;.
          </span>
        </div>
      </details>

      <div
        style={{
          fontSize: 13,
          color: color.text.primary,
          background: color.bg.page,
          border: `1px solid ${color.border.subtle}`,
          borderRadius: radius.xs,
          padding: 8,
          lineHeight: 1.5,
        }}
      >
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
    <div
      style={{
        background: color.bg.page,
        borderRadius: radius.lg,
        padding: 24,
        boxShadow: shadow.modal,
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      <div>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: color.text.primary }}>Create a trigger</h2>
        <p style={{ margin: "6px 0 0", fontSize: 13, color: color.text.secondary }}>
          Triggers monitor documents or folders and send events when a specified change occurs.
        </p>
      </div>
      <div style={fieldStyle}>
        <span style={fieldLabelStyle}>Watching</span>
        <div style={{ ...inputStyle, color: color.text.primary }}>{scope}</div>
      </div>
      <div style={fieldStyle}>
        <span style={fieldLabelStyle}>If</span>
        <div style={{ ...inputStyle, color: color.text.primary, whiteSpace: "pre-wrap" }}>{ifText}</div>
      </div>
      <div style={fieldStyle}>
        <span style={fieldLabelStyle}>Then send</span>
        <div style={{ ...inputStyle, color: color.text.primary, whiteSpace: "pre-wrap" }}>{sendText}</div>
      </div>
      <div style={fieldStyle}>
        <span style={fieldLabelStyle}>To</span>
        <div style={{ ...inputStyle, color: color.text.primary }}>{destLabel}</div>
      </div>
    </div>
  );
}

const fieldStyle: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 6 };

const fieldLabelStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  color: color.text.muted,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
};

const fieldHintStyle: React.CSSProperties = {
  fontSize: 12,
  color: color.text.muted,
  lineHeight: 1.4,
};

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  border: `1px solid ${color.border.default}`,
  borderRadius: radius.sm,
  fontSize: 14,
  outline: "none",
  width: "100%",
  boxSizing: "border-box",
  background: color.bg.page,
};

"use client";

import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { Button, InputTypeIn } from "@onyx-ai/opal/components";
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
import { SelectButton } from "@onyx-ai/opal/components";

import { SlackDestinationPicker } from "@/components/triggers/SlackDestinationPicker";
import { ensureEmailDestination } from "@/lib/emailConnect";
import { useSlackConnectStatus } from "@/lib/slackConnect";
import {
  createTrigger,
  updateTrigger,
  useDestinationConfigs,
  type Trigger,
  type TriggerCreateInput,
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

const EXAMPLE_IF = "the document is updated with a release version";
const EXAMPLE_SEND =
  "a message saying that the version has been finalized or updated to the specific version number.";

export function TriggerModal({
  open,
  initial,
  onClose,
  onSaved,
  lockScope,
}: Props) {
  const isEdit = Boolean(initial?.id);
  const [scopePath, setScopePath] = useState("");
  const [ifText, setIfText] = useState("");
  const [sendText, setSendText] = useState("");
  const { configs, refresh: refreshConfigs } = useDestinationConfigs();
  const { status: slackStatus } = useSlackConnectStatus();
  const [destinationConfigId, setDestinationConfigId] = useState<string | null>(
    null,
  );
  const [kind, setKind] = useState<TriggerKind>("delta");
  const [scheduleParts, setScheduleParts] = useState(defaultScheduleParts());
  const [customCron, setCustomCron] = useState("");
  const [tz, setTz] = useState(browserTimezone());
  const [startAtLocal, setStartAtLocal] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [emailMode, setEmailMode] = useState(false);
  const [emailDraft, setEmailDraft] = useState("");
  const [emailCommitting, setEmailCommitting] = useState(false);
  const emailInputRef = useRef<HTMLInputElement>(null);

  const tzOptions = useMemo(() => listTimezones(), []);

  // Selecting an email destination (edit flow or picker) fills the input
  // beneath the picker with its address.
  const selectedForDraft = configs.find((c) => c.id === destinationConfigId);
  useEffect(() => {
    if (selectedForDraft?.type === "email") {
      setEmailDraft(String(selectedForDraft.config.address ?? ""));
    }
  }, [selectedForDraft]);

  useEffect(() => {
    if (!open) return;
    setScopePath(initial?.scope_path ?? "");
    setIfText(initial?.nl_description ?? "");
    const firstAction = initial?.actions?.[0];
    setSendText(firstAction?.message ?? "");
    setDestinationConfigId(firstAction?.destination_config_id ?? null);
    setKind((initial?.kind as TriggerKind) ?? "delta");
    const parts = cronToParts(initial?.schedule_cron ?? null);
    setScheduleParts(parts);
    setCustomCron(
      parts.preset === "custom" ? (initial?.schedule_cron ?? "") : "",
    );
    setTz(initial?.schedule_timezone ?? browserTimezone());
    setStartAtLocal(utcIsoToLocalInput(initial?.schedule_start_at ?? null));
    setError(null);
    setEmailMode(false);
    setEmailDraft("");
  }, [
    open,
    initial?.id,
    initial?.scope_path,
    initial?.nl_description,
    initial?.actions,
    initial?.kind,
    initial?.schedule_cron,
    initial?.schedule_timezone,
    initial?.schedule_start_at,
  ]);

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
        actions: [{ destination_config_id: destinationConfigId, message: msg }],
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
          actions: [
            { destination_config_id: destinationConfigId, message: msg },
          ],
          schedule_cron: kind === "schedule" ? computedCron : null,
          schedule_timezone: kind === "schedule" ? tz : null,
          schedule_start_at:
            kind === "schedule" ? localInputToUtcIso(startAtLocal) : null,
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
    (kind === "delta" || (computedCron && tz));
  const selectedConfig = configs.find((c) => c.id === destinationConfigId);
  const selectedIsEmail = selectedConfig?.type === "email";
  const destDescription = selectedIsEmail
    ? selectedConfig?.verified_at
      ? `Fires will be emailed to ${selectedConfig.name}.`
      : `A verification link was sent to ${selectedConfig?.name}. Delivery starts once it is clicked.`
    : emailMode
      ? "Type the address and press Enter."
      : selectedConfig
        ? ""
        : "Tracked in the event log only.";

  async function commitEmail() {
    if (emailCommitting) return;
    const address = emailDraft.trim();
    if (!address.includes("@")) {
      setError("enter a valid email address");
      return;
    }
    // Re-committing the already-selected address is a no-op.
    if (
      selectedIsEmail &&
      String(selectedConfig?.config.address ?? "").toLowerCase() ===
        address.toLowerCase()
    ) {
      emailInputRef.current?.blur();
      return;
    }
    setError(null);
    setEmailCommitting(true);
    try {
      const { id, verificationError } = await ensureEmailDestination(
        configs,
        address,
      );
      setDestinationConfigId(id);
      emailInputRef.current?.blur();
      await refreshConfigs();
      if (verificationError) setError(verificationError);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to add address");
    } finally {
      setEmailCommitting(false);
    }
  }

  return (
    <div
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      className="fixed inset-0 z-[100] flex items-center justify-center bg-(--mask-03)"
    >
      <form
        onSubmit={onSubmit}
        className="relative z-[1] flex max-h-[92vh] w-[min(560px,92vw)] flex-col gap-4 overflow-y-auto rounded-(--border-radius-12) bg-(--background-tint-00) p-6 shadow-(--shadow-modal)"
      >
        <div>
          <h2 className="m-0 text-lg font-semibold text-(--text-05)">
            {isEdit ? "Edit trigger" : "Create a trigger"}
          </h2>
          <p className="mt-[6px] mb-0 text-[13px] leading-[1.55] text-(--text-04)">
            Triggers monitor documents or folders and send events when a
            specified condition is met. They can fire on document updates or on
            a recurring schedule.
          </p>
        </div>

        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-bold tracking-[0.06em] text-(--text-03) uppercase">
            When to run
          </span>
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as TriggerKind)}
            disabled={busy || isEdit}
            className={`box-border w-full rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none ${isEdit ? "cursor-not-allowed" : "cursor-pointer"}`}
          >
            <option value="delta">On a document update</option>
            <option value="schedule">On a schedule</option>
          </select>
          {isEdit && (
            <span className="text-xs leading-[1.4] text-(--text-03)">
              The trigger type can&rsquo;t be changed after creation. Delete and
              recreate to switch.
            </span>
          )}
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-bold tracking-[0.06em] text-(--text-03) uppercase">
            Watching
          </span>
          <input
            value={scopePath}
            onChange={(e) => setScopePath(e.target.value)}
            disabled={busy || lockScope}
            placeholder="projects/foo.md or projects"
            className="box-border w-full rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
          />
          <span className="text-xs leading-[1.4] text-(--text-03)">
            e.g. <code>projects/foo.md</code> for one document,{" "}
            <code>projects</code> for a folder, or <code>/</code> to watch the
            whole wiki.
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
          <span className="text-[11px] font-bold tracking-[0.06em] text-(--text-03) uppercase">
            If
          </span>
          <textarea
            value={ifText}
            onChange={(e) => setIfText(e.target.value)}
            disabled={busy}
            placeholder={EXAMPLE_IF}
            rows={2}
            className="box-border w-full resize-y rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
          />
          {kind === "schedule" && (
            <span className="text-xs leading-[1.4] text-(--text-03)">
              On each scheduled run, the trigger fires only when this condition
              is satisfied by the current state of the documents under{" "}
              <em>Watching</em>.
            </span>
          )}
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-bold tracking-[0.06em] text-(--text-03) uppercase">
            Then send
          </span>
          <textarea
            value={sendText}
            onChange={(e) => setSendText(e.target.value)}
            disabled={busy}
            placeholder={EXAMPLE_SEND}
            rows={2}
            className="box-border w-full resize-y rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
          />
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-bold tracking-[0.06em] text-(--text-03) uppercase">
            To
          </span>
          <SlackDestinationPicker
            configs={configs}
            includeExisting
            value={destinationConfigId}
            connected={Boolean(slackStatus?.connected)}
            disabled={busy}
            onPick={async (id) => {
              setError(null);
              setEmailMode(false);
              setDestinationConfigId(id);
              await refreshConfigs();
            }}
            onPickEmail={() => {
              setError(null);
              setEmailMode(true);
            }}
            onError={(m) => setError(m)}
          >
            <SelectButton size="sm" state="empty" width="full">
              {emailMode && !selectedIsEmail
                ? "Email"
                : selectedConfig
                  ? selectedConfig.name
                  : "Event log"}
            </SelectButton>
          </SlackDestinationPicker>
          {(emailMode || selectedIsEmail) && (
            <InputTypeIn
              ref={emailInputRef}
              autoFocus={emailMode && !selectedIsEmail}
              placeholder="name@example.com — Enter to add"
              value={emailDraft}
              onChange={(e) => setEmailDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void commitEmail();
                }
              }}
            />
          )}
          {destDescription && (
            <span className="text-xs leading-[1.4] text-(--text-03)">
              {destDescription}
            </span>
          )}
        </label>

        {error && (
          <div className="rounded-(--border-radius-04) bg-(--status-error-01) p-[10px] text-[13px] text-(--status-text-error-05)">
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
  const showTimeOfDay =
    parts.preset === "daily" ||
    parts.preset === "weekly" ||
    parts.preset === "monthly";
  const showWeekday = parts.preset === "weekly";
  const showDayOfMonth = parts.preset === "monthly";
  const isCustom = parts.preset === "custom";

  const timeValue = `${pad(parts.hour)}:${pad(parts.minute)}`;

  return (
    <div className="flex flex-col gap-3 rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-01) p-[14px]">
      <label className="flex flex-col gap-1.5">
        <span className="text-[11px] font-bold tracking-[0.06em] text-(--text-03) uppercase">
          Frequency
        </span>
        <select
          value={parts.preset}
          onChange={(e) =>
            onPartsChange({
              ...parts,
              preset: e.target.value as FrequencyPreset,
            })
          }
          disabled={disabled}
          className="box-border w-full cursor-pointer rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
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
          <span className="text-[11px] font-bold tracking-[0.06em] text-(--text-03) uppercase">
            Time of day
          </span>
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
            className="box-border w-full rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
          />
          <span className="text-xs leading-[1.4] text-(--text-03)">
            Interpreted in the timezone selected below.
          </span>
        </label>
      )}

      {showWeekday && (
        <label className="flex flex-col gap-1.5">
          <span className="text-[11px] font-bold tracking-[0.06em] text-(--text-03) uppercase">
            Day of week
          </span>
          <select
            value={parts.dayOfWeek}
            onChange={(e) =>
              onPartsChange({ ...parts, dayOfWeek: Number(e.target.value) })
            }
            disabled={disabled}
            className="box-border w-full cursor-pointer rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
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
          <span className="text-[11px] font-bold tracking-[0.06em] text-(--text-03) uppercase">
            Day of month
          </span>
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
            className="box-border w-full rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
          />
          <span className="text-xs leading-[1.4] text-(--text-03)">
            Months without this day (e.g. day 31 in February) skip that month
            entirely &mdash; the schedule does not roll over to the next valid
            day.
          </span>
        </label>
      )}

      <label className="flex flex-col gap-1.5">
        <span className="text-[11px] font-bold tracking-[0.06em] text-(--text-03) uppercase">
          Timezone
        </span>
        <select
          value={tz}
          onChange={(e) => onTzChange(e.target.value)}
          disabled={disabled}
          className="box-border w-full cursor-pointer rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
        >
          {tzOptions.includes(tz) ? null : <option value={tz}>{tz}</option>}
          {tzOptions.map((zone) => (
            <option key={zone} value={zone}>
              {zone}
            </option>
          ))}
        </select>
        <span className="text-xs leading-[1.4] text-(--text-03)">
          The schedule runs in this timezone. Daylight-saving transitions are
          handled automatically.
        </span>
      </label>

      <label className="flex flex-col gap-1.5">
        <span className="text-[11px] font-bold tracking-[0.06em] text-(--text-03) uppercase">
          Do not fire before (optional)
        </span>
        <input
          type="datetime-local"
          value={startAtLocal}
          onChange={(e) => onStartAtChange(e.target.value)}
          disabled={disabled}
          className="box-border w-full rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
        />
        <span className="text-xs leading-[1.4] text-(--text-03)">
          Anchored to your local time. Leave empty to start at the next
          scheduled run. Useful for delaying a launch (e.g. &ldquo;don&rsquo;t
          start until next Monday&rdquo;).
        </span>
      </label>

      <details>
        <summary className="cursor-pointer text-xs font-semibold text-(--text-04) select-none">
          Advanced &mdash; raw cron expression
        </summary>
        <div className="mt-[10px] flex flex-col gap-2">
          <div className="grid grid-cols-5 gap-1.5">
            {CRON_FIELD_HELP.map((f) => (
              <div key={f.label}>
                <div className="text-[10px] font-bold tracking-[0.06px] text-(--text-03) uppercase">
                  {f.label}
                </div>
                <div className="text-[11px] leading-[1.4] text-(--text-02)">
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
            className="box-border w-full rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 font-mono text-sm outline-none"
          />
          <span className="text-xs leading-[1.4] text-(--text-03)">
            Standard 5-field cron. Editing this switches the frequency to
            &ldquo;Custom&rdquo;.
          </span>
        </div>
      </details>

      <div className="rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) p-2 text-[13px] leading-[1.5] text-(--text-05)">
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

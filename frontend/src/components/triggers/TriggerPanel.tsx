"use client";

import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import useSWR from "swr";

import {
  Button,
  Divider,
  FilterButton,
  InputTypeIn,
  LineItemButton,
  Popover,
  PopoverMenu,
  Tabs,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgBook,
  SvgFile,
  SvgFolder,
  SvgPlusCircle,
  SvgWorkflow,
  SvgX,
} from "@onyx-ai/opal/icons";
import { markdown } from "@onyx-ai/opal/utils";
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
  ActionEditor,
  type ActionGroup,
} from "@/components/triggers/ActionEditor";
import { useSlackConnectStatus } from "@/lib/slackConnect";
import {
  createTrigger,
  updateTrigger,
  useDestinationConfigs,
  type Trigger,
  type TriggerActionInput,
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

let groupKeyCounter = 1;
const nextGroupKey = () => ++groupKeyCounter;

/** Rebuild editor groups from a trigger's flat action list: actions sharing a
 * destination type and message collapse into one group with recipient chips.
 * An action whose config no longer exists is already recorded-only at
 * dispatch, so it lands in an Activity Center group keeping its message. */
function groupsFromActions(
  actions: Trigger["actions"] | undefined,
  configs: { id: string; type: string }[],
): ActionGroup[] {
  const out: ActionGroup[] = [];
  for (const a of actions ?? []) {
    const message = a.message ?? "";
    const cfg = a.destination_config_id
      ? configs.find((c) => c.id === a.destination_config_id)
      : undefined;
    const type: ActionGroup["type"] =
      cfg?.type === "slack" || cfg?.type === "email" ? cfg.type : "event_log";
    const existing = out.find((g) => g.type === type && g.message === message);
    if (existing) {
      if (cfg && !existing.configIds.includes(cfg.id))
        existing.configIds.push(cfg.id);
      continue;
    }
    out.push({
      key: nextGroupKey(),
      type,
      configIds: cfg ? [cfg.id] : [],
      message,
    });
  }
  if (!out.length)
    out.push({
      key: nextGroupKey(),
      type: "event_log",
      configIds: [],
      message: "",
    });
  return out;
}

const EXAMPLE_IF = "the document is updated with a release version";

export function TriggerPanel({
  open,
  initial,
  onClose,
  onSaved,
  lockScope,
}: Props) {
  const isEdit = Boolean(initial?.id);
  const [scopePath, setScopePath] = useState("");
  const [ifText, setIfText] = useState("");
  const { configs, refresh: refreshConfigs } = useDestinationConfigs();
  const { status: slackStatus } = useSlackConnectStatus();
  const [groups, setGroups] = useState<ActionGroup[]>([
    { key: 1, type: "event_log", configIds: [], message: "" },
  ]);
  // Hydration reads configs through a ref so the open-reset effect doesn't
  // re-fire (and clobber edits) when the SWR config list revalidates.
  const configsRef = useRef(configs);
  configsRef.current = configs;
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
    setGroups(groupsFromActions(initial?.actions, configsRef.current));
    setKind((initial?.kind as TriggerKind) ?? "delta");
    const parts = cronToParts(initial?.schedule_cron ?? null);
    setScheduleParts(parts);
    setCustomCron(
      parts.preset === "custom" ? (initial?.schedule_cron ?? "") : "",
    );
    setTz(initial?.schedule_timezone ?? browserTimezone());
    setStartAtLocal(utcIsoToLocalInput(initial?.schedule_start_at ?? null));
    setError(null);
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
      const actions: TriggerActionInput[] = groups.flatMap(
        (g): TriggerActionInput[] =>
          g.type === "event_log"
            ? [{ destination_config_id: null, message: g.message.trim() }]
            : g.configIds.map((id) => ({
                destination_config_id: id,
                message: g.message.trim(),
              })),
      );
      const baseInput: TriggerCreateInput = {
        scope_path: scopePath.trim(),
        nl_description: nl,
        actions,
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
          actions,
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

  const groupsValid = groups.every(
    (g) =>
      g.message.trim() && (g.type === "event_log" || g.configIds.length > 0),
  );
  const canSave =
    scopePath.trim() &&
    ifText.trim() &&
    groupsValid &&
    (kind === "delta" || (computedCron && tz));

  const destinationSummary = groups
    .map((g) =>
      g.type === "event_log"
        ? "the Activity Center"
        : g.configIds
            .map((id) => configs.find((c) => c.id === id)?.name)
            .filter(Boolean)
            .join(", ") || (g.type === "slack" ? "Slack" : "Email"),
    )
    .join(" and ");

  return (
    <div className="fixed top-2 right-2 z-[100] flex max-h-[calc(100vh-16px)] w-[464px] max-w-[calc(100vw-16px)] flex-col">
      <form
        onSubmit={onSubmit}
        className="flex max-h-full w-full flex-col overflow-hidden rounded-(--radius-12) border border-(--border-01) bg-(--background-tint-00)"
      >
        <div className="flex w-full items-start gap-2 p-2">
          <div className="flex min-w-0 flex-1 items-start gap-[2px] p-[2px]">
            <span className="flex size-6 items-center justify-center p-1">
              <SvgWorkflow size={18} />
            </span>
            <div className="min-w-0 flex-1 px-[2px]">
              <Text as="p" font="main-content-emphasis" color="text-04">
                {isEdit ? "Edit Trigger" : "New Trigger"}
              </Text>
              <Text as="p" font="secondary-body" color="text-03">
                Trigger actions on changes or specified conditions.
              </Text>
            </div>
          </div>
          <Button
            type="button"
            icon={SvgX}
            size="sm"
            tooltip="Close"
            onClick={onClose}
            disabled={busy}
          />
        </div>

        <div className="flex w-full flex-1 flex-col gap-3 overflow-y-auto bg-(--background-tint-01) p-3">
          <Tabs
            value={kind}
            onValueChange={(v) => {
              if (!isEdit && !busy) setKind(v as TriggerKind);
            }}
            variant="contained"
          >
            <Tabs.List>
              <Tabs.Trigger
                value="delta"
                disabled={isEdit && kind !== "delta"}
                tooltip={
                  isEdit
                    ? "The trigger type can't be changed after creation"
                    : undefined
                }
              >
                Run on Wiki Updates
              </Tabs.Trigger>
              <Tabs.Trigger
                value="schedule"
                disabled={isEdit && kind !== "schedule"}
                tooltip={
                  isEdit
                    ? "The trigger type can't be changed after creation"
                    : undefined
                }
              >
                Recurring Schedule
              </Tabs.Trigger>
            </Tabs.List>
          </Tabs>

          <div className="flex w-full flex-col gap-1">
            <div className="px-[2px]">
              <Text font="main-ui-action" color="text-04">
                Watch
              </Text>
            </div>
            <WatchScopePicker
              scopePath={scopePath}
              onScopePath={(p) => {
                setScopePath(p);
              }}
              disabled={busy || Boolean(lockScope)}
              locked={Boolean(lockScope)}
            />
            <div className="px-[2px]">
              <Text font="secondary-body" color="text-03">
                Add a specific page or an entire folder to watch.
              </Text>
            </div>
          </div>

          <Divider />

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

          <div className="flex w-full flex-col gap-1">
            <div className="px-[2px]">
              <Text font="main-ui-action" color="text-04">
                Run if
              </Text>
            </div>
            {/* raw-ok: no Opal multiline input */}
            <textarea
              value={ifText}
              onChange={(e) => setIfText(e.target.value)}
              disabled={busy}
              placeholder={EXAMPLE_IF}
              rows={2}
              className="box-border w-full resize-y rounded-(--radius-08) border border-(--border-02) bg-(--background-tint-00) px-[10px] py-2 text-[14px] leading-5 outline-none placeholder:text-(--text-02) focus:border-(--border-05) focus:shadow-[0_0_0_2px_var(--background-tint-04)]"
            />
            {kind === "schedule" && (
              <div className="px-[2px]">
                <Text font="secondary-body" color="text-03">
                  On each scheduled run, the trigger fires only when this
                  condition is satisfied by the watched documents.
                </Text>
              </div>
            )}
          </div>

          <ActionEditor
            groups={groups}
            onChange={setGroups}
            configs={configs}
            refreshConfigs={refreshConfigs}
            slackConnected={Boolean(slackStatus?.connected)}
            disabled={busy}
            onError={(m) => setError(m)}
          />
          <div className="flex w-full items-center">
            <Button
              type="button"
              icon={SvgPlusCircle}
              disabled={busy}
              onClick={() =>
                setGroups([
                  ...groups,
                  {
                    key: nextGroupKey(),
                    type: slackStatus?.connected ? "slack" : "email",
                    configIds: [],
                    message: "",
                  },
                ])
              }
            >
              Add More Actions
            </Button>
          </div>

          {error && (
            <div className="rounded-(--radius-08) bg-(--status-error-01) p-[10px] text-[13px] text-(--status-text-error-05)">
              {error}
            </div>
          )}
        </div>

        <div className="flex w-full items-center gap-2 border-t border-(--border-01) bg-(--background-tint-00) p-3">
          <div className="min-w-0 flex-1 px-[2px]">
            <Text as="p" font="secondary-body" color="text-03">
              {markdown(
                `Messages will be sent to **${destinationSummary}** when conditions are met.`,
              )}
            </Text>
          </div>
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
        <Text font="main-ui-action" color="text-04">
          Frequency
        </Text>
        {/* raw-ok: no Opal multiline input */}
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
          <Text font="main-ui-action" color="text-04">
            Time of day
          </Text>
          {/* raw-ok: no Opal multiline input */}
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
          <Text font="secondary-body" color="text-03">
            Interpreted in the timezone selected below.
          </Text>
        </label>
      )}

      {showWeekday && (
        <label className="flex flex-col gap-1.5">
          <Text font="main-ui-action" color="text-04">
            Day of week
          </Text>
          {/* raw-ok: no Opal multiline input */}
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
          <Text font="main-ui-action" color="text-04">
            Day of month
          </Text>
          {/* raw-ok: no Opal multiline input */}
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
          <Text font="secondary-body" color="text-03">
            Months without this day (e.g. day 31 in February) skip that month
            entirely &mdash; the schedule does not roll over to the next valid
            day.
          </Text>
        </label>
      )}

      <label className="flex flex-col gap-1.5">
        <Text font="main-ui-action" color="text-04">
          Timezone
        </Text>
        {/* raw-ok: no Opal multiline input */}
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
        <Text font="secondary-body" color="text-03">
          The schedule runs in this timezone. Daylight-saving transitions are
          handled automatically.
        </Text>
      </label>

      <label className="flex flex-col gap-1.5">
        <Text font="main-ui-action" color="text-04">
          Do not fire before (optional)
        </Text>
        {/* raw-ok: no Opal multiline input */}
        <input
          type="datetime-local"
          value={startAtLocal}
          onChange={(e) => onStartAtChange(e.target.value)}
          disabled={disabled}
          className="box-border w-full rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
        />
        <Text font="secondary-body" color="text-03">
          Anchored to your local time. Leave empty to start at the next
          scheduled run. Useful for delaying a launch (e.g. &ldquo;don&rsquo;t
          start until next Monday&rdquo;).
        </Text>
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
          {/* raw-ok: no Opal multiline input */}
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
          <Text font="secondary-body" color="text-03">
            Standard 5-field cron. Editing this switches the frequency to
            &ldquo;Custom&rdquo;.
          </Text>
        </div>
      </details>

      <div className="rounded-(--radius-04) border border-(--border-01) bg-(--background-tint-00) p-2">
        <Text font="main-ui-action" color="text-05">
          {cronSummary}
        </Text>
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

const WATCH_CHIP_BAR =
  "flex min-h-[36px] w-full flex-wrap content-center items-center gap-1 rounded-(--radius-08) border border-(--border-02) bg-(--background-tint-00) p-[6px] focus-within:border-(--border-05) focus-within:shadow-[0_0_0_2px_var(--background-tint-04)]";

/** Search-and-pick for the trigger's watched scope: a dropdown over the
 * ACL-filtered wiki path list (files and their folders), selection only —
 * free-typed paths can't be committed, so a scope always exists. */
function WatchScopePicker({
  scopePath,
  onScopePath,
  disabled,
  locked,
}: {
  scopePath: string;
  onScopePath: (path: string) => void;
  disabled?: boolean;
  locked?: boolean;
}) {
  const { data } = useSWR<{ entries: { path: string }[] }>("/wiki");
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const anchorRef = useRef<HTMLDivElement>(null);

  const files = useMemo(
    () =>
      (data?.entries ?? [])
        .map((e) => e.path)
        .filter(
          (p) => p.endsWith(".md") && !p.split("/").pop()?.startsWith("."),
        ),
    [data],
  );
  const folders = useMemo(() => {
    const out = new Set<string>();
    for (const f of files) {
      const parts = f.split("/");
      for (let i = 1; i < parts.length; i++)
        out.add(parts.slice(0, i).join("/"));
    }
    return [...out].sort();
  }, [files]);

  const q = query.trim().toLowerCase();
  const matchedFolders = folders
    .filter((f) => !q || f.toLowerCase().includes(q))
    .slice(0, 8);
  const matchedFiles = files
    .filter((f) => !q || f.toLowerCase().includes(q))
    .slice(0, 20);

  function pick(path: string) {
    onScopePath(path);
    setQuery("");
    setOpen(false);
  }

  const committed = scopePath.trim();

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Popover.Anchor asChild>
        <div ref={anchorRef} className={WATCH_CHIP_BAR}>
          {committed ? (
            <FilterButton
              icon={
                committed === "/"
                  ? SvgBook
                  : committed.endsWith(".md")
                    ? SvgFile
                    : SvgFolder
              }
              active={!locked}
              onClear={() => {
                if (!disabled && !locked) onScopePath("");
              }}
              disabled={disabled}
            >
              {committed === "/" ? "Whole wiki" : committed}
            </FilterButton>
          ) : (
            <div className="min-w-[120px] flex-1">
              <InputTypeIn
                variant="internal"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  if (!open) setOpen(true);
                }}
                onFocus={() => setOpen(true)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setOpen(false);
                  if (e.key === "Enter") {
                    e.preventDefault();
                    // Enter commits the visually top row: Whole wiki leads an
                    // empty query, then folders, then files.
                    const first = !q
                      ? "/"
                      : (matchedFolders[0] ?? matchedFiles[0]);
                    if (first) pick(first);
                  }
                }}
                placeholder="Search pages and folders to watch"
              />
            </div>
          )}
        </div>
      </Popover.Anchor>
      <Popover.Content
        width="trigger"
        align="start"
        sideOffset={4}
        onOpenAutoFocus={(e) => e.preventDefault()}
        onInteractOutside={(e) => {
          if (anchorRef.current?.contains(e.target as Node)) e.preventDefault();
        }}
      >
        <PopoverMenu>
          {!q && (
            <LineItemButton
              icon={SvgBook}
              title="Whole wiki"
              sizePreset="main-ui"
              variant="body"
              state="empty"
              onClick={() => pick("/")}
            />
          )}
          {matchedFolders.map((f) => (
            <LineItemButton
              key={`d:${f}`}
              icon={SvgFolder}
              title={f}
              sizePreset="main-ui"
              variant="body"
              state="empty"
              onClick={() => pick(f)}
            />
          ))}
          {matchedFiles.map((f) => (
            <LineItemButton
              key={`f:${f}`}
              icon={SvgFile}
              title={f}
              sizePreset="main-ui"
              variant="body"
              state="empty"
              onClick={() => pick(f)}
            />
          ))}
          {!matchedFiles.length && !matchedFolders.length && (
            <LineItemButton
              title="No matches"
              sizePreset="main-ui"
              variant="body"
              state="empty"
              onClick={() => undefined}
            />
          )}
        </PopoverMenu>
      </Popover.Content>
    </Popover>
  );
}

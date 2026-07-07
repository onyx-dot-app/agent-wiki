"use client";

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";

import useSWR from "swr";

import {
  Button,
  Divider,
  LineItemButton,
  Popover,
  PopoverMenu,
  Tabs,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgBook,
  SvgFold,
  SvgFile,
  SvgFolder,
  SvgPlusCircle,
  SvgWorkflow,
  SvgX,
} from "@onyx-ai/opal/icons";
import { Content } from "@onyx-ai/opal/layouts";
import { cn, markdown } from "@onyx-ai/opal/utils";
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
import InputChipField from "@/components/inputs/InputChipField";
import InputTextArea from "@/components/inputs/InputTextArea";
import { useSlackConnectStatus } from "@/lib/slackConnect";
import {
  createTrigger,
  type TriggerScope,
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
  /** Shown as a Delete button in the footer on edit; the caller owns the
   * confirm + request and closes the panel by resolving. */
  onDelete?: () => void | Promise<void>;
  /** Lock the scope_path input so callers (e.g. doc page) can pin it. */
  lockScope?: boolean;
  /** Render as a docked right-panel column instead of the floating overlay
   * (the doc page portals it into the right-panel host per the mock). */
  docked?: boolean;
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

/** Vertical label + control + helper stack. Composes Opal's Content for the
 * label row; InputVertical is unsuitable here (its root is h-full, sized for
 * fixed-height rows, and collapses in a free-flow column). */
function LabeledField({
  title,
  helper,
  htmlFor,
  children,
}: {
  title: string;
  helper?: string;
  /** id of the native control below, so the visible title is a real label. */
  htmlFor?: string;
  children: React.ReactNode;
}) {
  const heading = (
    <Content title={title} sizePreset="main-ui" variant="section" />
  );
  return (
    <div className="flex w-full flex-col gap-1">
      {htmlFor ? <label htmlFor={htmlFor}>{heading}</label> : heading}
      {children}
      {helper && (
        <div className="px-[2px]">
          <Text font="secondary-body" color="text-03">
            {helper}
          </Text>
        </div>
      )}
    </div>
  );
}

const EXAMPLE_IF = "the document is updated with a release version";

export function TriggerPanel({
  open,
  initial,
  onClose,
  onSaved,
  onDelete,
  lockScope,
  docked,
}: Props) {
  const isEdit = Boolean(initial?.id);
  const [scopes, setScopes] = useState<TriggerScope[]>([]);
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
    setScopes(
      initial?.scopes?.length
        ? initial.scopes
        : initial?.scope_path
          ? [{ path: initial.scope_path }]
          : [],
    );
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
        scope_path: scopes[0]?.path ?? "",
        scopes,
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
          scopes,
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
    scopes.length > 0 &&
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
    <div
      className={
        docked
          ? "flex w-full flex-col"
          : "fixed top-2 right-2 z-[100] flex max-h-[calc(100vh-16px)] w-[464px] max-w-[calc(100vw-16px)] flex-col"
      }
    >
      <form
        onSubmit={onSubmit}
        className={cn(
          "flex w-full flex-col overflow-hidden rounded-(--radius-12) border border-(--border-01) bg-(--background-tint-00)",
          !docked && "max-h-full",
        )}
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
            icon={docked ? SvgFold : SvgX}
            size="sm"
            prominence="internal"
            tooltip={docked ? "Collapse" : "Close"}
            onClick={onClose}
            disabled={busy}
          />
        </div>

        <div
          className={cn(
            "flex w-full flex-1 flex-col gap-3 bg-(--background-tint-01) p-3",
            !docked && "overflow-y-auto",
          )}
        >
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

          <LabeledField
            title="Watch"
            helper="Add specific sections or entire pages to watch."
          >
            <WatchScopePicker
              scopes={scopes}
              onScopes={setScopes}
              disabled={busy || Boolean(lockScope)}
              locked={Boolean(lockScope)}
            />
          </LabeledField>

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

          <LabeledField title="Run if">
            <InputTextArea
              value={ifText}
              onChange={(e) => setIfText(e.target.value)}
              variant={busy ? "disabled" : "primary"}
              placeholder={EXAMPLE_IF}
              rows={2}
            />
            {kind === "schedule" && (
              <div className="px-[2px]">
                <Text font="secondary-body" color="text-03">
                  On each scheduled run, the trigger fires only when this
                  condition is satisfied by the watched documents.
                </Text>
              </div>
            )}
          </LabeledField>

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
              prominence="secondary"
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
          {isEdit && onDelete && (
            <Button
              type="button"
              variant="danger"
              prominence="secondary"
              disabled={busy}
              onClick={() => void onDelete()}
            >
              Delete
            </Button>
          )}
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

  // Unique per mounted panel so a second open panel can't duplicate ids or
  // let a label focus another instance's control.
  const uid = useId();
  const id = (field: string) => `${uid}-${field}`;

  return (
    <div className="flex flex-col gap-3 rounded-(--radius-04) border border-(--border-01) bg-(--background-tint-01) p-[14px]">
      <LabeledField title="Frequency" htmlFor={id("frequency")}>
        {/* raw-ok: no Opal multiline input */}
        <select
          id={id("frequency")}
          value={parts.preset}
          onChange={(e) =>
            onPartsChange({
              ...parts,
              preset: e.target.value as FrequencyPreset,
            })
          }
          disabled={disabled}
          className="box-border w-full cursor-pointer rounded-(--radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
        >
          {PRESET_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </LabeledField>

      {showTimeOfDay && (
        <LabeledField title="Time of day" htmlFor={id("time")}>
          {/* raw-ok: no Opal multiline input */}
          <input
            id={id("time")}
            type="time"
            value={timeValue}
            onChange={(e) => {
              const [h, m] = e.target.value.split(":").map(Number);
              if (Number.isFinite(h) && Number.isFinite(m)) {
                onPartsChange({ ...parts, hour: h, minute: m });
              }
            }}
            disabled={disabled}
            className="box-border w-full rounded-(--radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
          />
          <Text font="secondary-body" color="text-03">
            Interpreted in the timezone selected below.
          </Text>
        </LabeledField>
      )}

      {showWeekday && (
        <LabeledField title="Day of week" htmlFor={id("weekday")}>
          {/* raw-ok: no Opal multiline input */}
          <select
            id={id("weekday")}
            value={parts.dayOfWeek}
            onChange={(e) =>
              onPartsChange({ ...parts, dayOfWeek: Number(e.target.value) })
            }
            disabled={disabled}
            className="box-border w-full cursor-pointer rounded-(--radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
          >
            {WEEKDAY_NAMES.map((name, i) => (
              <option key={i} value={i}>
                {name}
              </option>
            ))}
          </select>
        </LabeledField>
      )}

      {showDayOfMonth && (
        <LabeledField title="Day of month" htmlFor={id("monthday")}>
          {/* raw-ok: no Opal multiline input */}
          <input
            id={id("monthday")}
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
            className="box-border w-full rounded-(--radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
          />
          <Text font="secondary-body" color="text-03">
            Months without this day (e.g. day 31 in February) skip that month
            entirely &mdash; the schedule does not roll over to the next valid
            day.
          </Text>
        </LabeledField>
      )}

      <LabeledField title="Timezone" htmlFor={id("timezone")}>
        {/* raw-ok: no Opal multiline input */}
        <select
          id={id("timezone")}
          value={tz}
          onChange={(e) => onTzChange(e.target.value)}
          disabled={disabled}
          className="box-border w-full cursor-pointer rounded-(--radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
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
      </LabeledField>

      <LabeledField
        title="Do not fire before (optional)"
        htmlFor={id("not-before")}
      >
        {/* raw-ok: no Opal multiline input */}
        <input
          id={id("not-before")}
          type="datetime-local"
          value={startAtLocal}
          onChange={(e) => onStartAtChange(e.target.value)}
          disabled={disabled}
          className="box-border w-full rounded-(--radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 text-sm outline-none"
        />
        <Text font="secondary-body" color="text-03">
          Anchored to your local time. Leave empty to start at the next
          scheduled run. Useful for delaying a launch (e.g. &ldquo;don&rsquo;t
          start until next Monday&rdquo;).
        </Text>
      </LabeledField>

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
            className="box-border w-full rounded-(--radius-04) border border-(--border-01) bg-(--background-tint-00) px-[10px] py-2 font-mono text-sm outline-none"
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

/** Search-and-pick for the trigger's watched scope: a dropdown over the
 * ACL-filtered wiki path list (files and their folders), selection only —
 * free-typed paths can't be committed, so a scope always exists. */
function scopeLabel(scope: TriggerScope): string {
  const base =
    scope.path === "" || scope.path === "/" ? "Whole wiki" : scope.path;
  if (scope.start_line == null) return base;
  const range =
    scope.end_line != null && scope.end_line !== scope.start_line
      ? `line ${scope.start_line}\u2013${scope.end_line}`
      : `line ${scope.start_line}`;
  return `${base} \u00b7 ${range}`;
}

function WatchScopePicker({
  scopes,
  onScopes,
  disabled,
  locked,
}: {
  scopes: TriggerScope[];
  onScopes: (scopes: TriggerScope[]) => void;
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
    // Whole wiki subsumes everything; otherwise append if not already watched.
    if (path === "/" || path === "") {
      onScopes([{ path: "/" }]);
    } else if (!scopes.some((s) => s.path === path)) {
      onScopes([...scopes.filter((s) => s.path !== "/"), { path }]);
    }
    setQuery("");
    setOpen(false);
  }

  function remove(index: number) {
    onScopes(scopes.filter((_, i) => i !== index));
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Popover.Anchor asChild>
        <div ref={anchorRef} className="w-full">
          <InputChipField
            chips={scopes.map((scope, i) => ({
              id: `${scope.path}#${scope.start_line ?? ""}#${i}`,
              label: scopeLabel(scope),
            }))}
            onRemoveChip={(id) => {
              if (disabled || locked) return;
              const i = scopes.findIndex(
                (scope, idx) =>
                  `${scope.path}#${scope.start_line ?? ""}#${idx}` === id,
              );
              if (i >= 0) remove(i);
            }}
            onAdd={() => {
              // Enter commits the visually top row: Whole wiki leads an
              // empty query, then folders, then files.
              const first = !q ? "/" : (matchedFolders[0] ?? matchedFiles[0]);
              if (first) pick(first);
            }}
            value={query}
            onChange={(v) => {
              setQuery(v);
              if (!open) setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setOpen(false);
            }}
            disabled={disabled || Boolean(locked)}
            placeholder={
              scopes.length
                ? locked
                  ? ""
                  : "Add another page or folder"
                : "Search pages and folders to watch"
            }
          />
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

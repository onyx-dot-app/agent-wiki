"use client";

import { useEffect, useState } from "react";

import {
  Button,
  Card,
  Switch,
  Table,
  Text,
  createTableColumns,
} from "@onyx-ai/opal/components";
import { SvgClock, SvgSliders } from "@onyx-ai/opal/icons";
import { ContentAction, InputErrorText } from "@onyx-ai/opal/layouts";

import {
  type AutoOrganizeSchedule,
  type DetectionRun,
  triggerSweep,
  updateAutoOrganizeSettings,
  useAutoOrganizeSettings,
  useDetectionRuns,
} from "@/lib/autoOrganize";

export function AutoOrganize() {
  const { settings, isLoading, error, refresh } = useAutoOrganizeSettings();
  // Fail safe until the real setting loads: an unknown/failed state shows the
  // feature off (switch off, schedule Off, sweep disabled), so an admin can't
  // act on a state the backend would reject.
  const enabled = settings?.enabled ?? false;
  const schedule: AutoOrganizeSchedule = settings?.schedule ?? "off";

  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  async function update(patch: {
    enabled?: boolean;
    schedule?: AutoOrganizeSchedule;
  }) {
    setBusy(true);
    setSaveError(null);
    try {
      // Seed the cache from the PUT's authoritative response (no revalidating
      // GET) so a rapid second change can't be clobbered by a stale in-flight
      // read.
      await refresh(await updateAutoOrganizeSettings(patch), {
        revalidate: false,
      });
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "failed to update setting");
    } finally {
      setBusy(false);
    }
  }

  const message = saveError || (error instanceof Error ? error.message : null);

  return (
    <div className="flex w-full flex-col gap-4">
      <Text font="main-ui-body" color="text-03">
        Run a detection sweep to find structural cleanups (such as empty
        folders). Cleanups in AI-managed scopes are applied automatically;
        others are proposed for review.
      </Text>

      <Card padding="xs" rounding="md" border="solid" background="heavy">
        <div className="flex w-full flex-col gap-1 p-1">
          <ContentAction
            sizePreset="main-ui"
            variant="section"
            icon={SvgSliders}
            title="Auto Organize"
            description="Master switch. When off, no sweeps run, nothing is auto-applied, and pending proposals are frozen."
            rightChildren={
              <Switch
                checked={enabled}
                disabled={isLoading || busy}
                onCheckedChange={(next) => void update({ enabled: next })}
              />
            }
          />
          <ContentAction
            sizePreset="main-ui"
            variant="section"
            icon={SvgSliders}
            title="Automatic sweeps"
            description="Run a scheduled whole-wiki sweep. Daily and weekly run at a fixed off-peak time (UTC)."
            rightChildren={
              <ScheduleSelector
                value={schedule}
                disabled={!enabled || isLoading || busy}
                onChange={(next) => void update({ schedule: next })}
              />
            }
          />
          {message && <InputErrorText type="error">{message}</InputErrorText>}
        </div>
      </Card>

      <SweepControl disabled={!enabled} />

      <RunHistory />
    </div>
  );
}

/** UTC second-granular DB text ("YYYY-MM-DD HH:MM:SS") → local display,
 * short form ("Jul 24, 9:25 AM"). */
function runTime(ts: string): string {
  const d = new Date(ts.replace(" ", "T") + "Z");
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function startedCell(_value: string, row: DetectionRun) {
  return (
    <Text font="main-ui-body" color="text-04">
      {`${runTime(row.started_at)} · ${row.triggered_by_user_id === null ? "system" : "manual"}`}
    </Text>
  );
}

function proposalsCell(_value: number, row: DetectionRun) {
  if (row.status !== "completed") {
    return (
      <Text font="main-ui-body" color="text-02">
        —
      </Text>
    );
  }
  return (
    <Text font="main-ui-body" color="text-04">
      {`${row.proposals_emitted} proposal${row.proposals_emitted === 1 ? "" : "s"}`}
    </Text>
  );
}

function scannedCell(_value: number, row: DetectionRun) {
  return (
    <Text font="main-ui-body" color="text-03">
      {row.status === "completed" ? `${row.paths_scanned} pages` : "—"}
    </Text>
  );
}

function statusCell(_value: string, row: DetectionRun) {
  if (row.status === "failed") {
    return (
      <Text font="main-ui-body" color="status-error-05">
        {row.error ?? "Failed"}
      </Text>
    );
  }
  return (
    <Text font="main-ui-body" color="text-03">
      {row.status === "running" ? "Running…" : "Completed"}
    </Text>
  );
}

const runColumns = (() => {
  const tc = createTableColumns<DetectionRun>();
  return [
    tc.column("started_at", {
      header: "Started",
      weight: 22,
      enableSorting: false,
      cell: startedCell,
    }),
    tc.column("proposals_emitted", {
      header: "Proposals",
      weight: 14,
      enableSorting: false,
      cell: proposalsCell,
    }),
    tc.column("paths_scanned", {
      header: "Scanned",
      weight: 14,
      enableSorting: false,
      cell: scannedCell,
    }),
    tc.column("status", {
      header: "Status",
      weight: 20,
      enableSorting: false,
      cell: statusCell,
    }),
  ];
})();

function RunHistory() {
  const { runs } = useDetectionRuns(false);
  if (runs.length === 0) return null;

  return (
    <div className="flex w-full flex-col gap-2">
      <ContentAction
        sizePreset="main-ui"
        variant="section"
        icon={SvgClock}
        title="Recent sweeps"
        description="What the last detection runs scanned and proposed."
      />
      <Table
        data={runs.slice(0, 20)}
        columns={runColumns}
        getRowId={(r) => r.id}
        variant="rows"
        size="md"
      />
    </div>
  );
}

const SCHEDULE_OPTIONS: { value: AutoOrganizeSchedule; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
];

interface ScheduleSelectorProps {
  value: AutoOrganizeSchedule;
  disabled: boolean;
  onChange: (next: AutoOrganizeSchedule) => void;
}

function ScheduleSelector({
  value,
  disabled,
  onChange,
}: ScheduleSelectorProps) {
  return (
    <div className="flex items-center gap-1">
      {SCHEDULE_OPTIONS.map((opt) => (
        <Button
          key={opt.value}
          type="button"
          size="sm"
          prominence={opt.value === value ? "primary" : "secondary"}
          disabled={disabled}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </Button>
      ))}
    </div>
  );
}

function SweepControl({ disabled }: { disabled: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The run id at the top of the history when the sweep was requested — the
  // request only *enqueues*, so the new run appears (and finishes) on its own
  // schedule; we watch the history until a different id lands and completes.
  const [watchFrom, setWatchFrom] = useState<string | null>(null);
  const watching = watchFrom !== null;
  const { runs, refresh } = useDetectionRuns(watching);

  const latest = runs[0];
  const newRun =
    watching && latest && latest.id !== watchFrom ? latest : undefined;
  const [lastFinished, setLastFinished] = useState<DetectionRun | null>(null);
  useEffect(() => {
    // Terminal state on the watched run — capture the outcome, stop polling.
    if (newRun && newRun.status !== "running") {
      setLastFinished(newRun);
      setWatchFrom(null);
    }
  }, [newRun]);

  async function run() {
    setBusy(true);
    setError(null);
    setLastFinished(null);
    try {
      // Snapshot the current top-of-history *before* enqueueing so the new
      // run is recognized by id, not by racy timestamp comparison.
      setWatchFrom(runs[0]?.id ?? "");
      await triggerSweep();
      void refresh();
    } catch (e) {
      setWatchFrom(null);
      setError(e instanceof Error ? e.message : "failed to start sweep");
    } finally {
      setBusy(false);
    }
  }

  const note = watching
    ? newRun
      ? "Sweep running…"
      : "Sweep queued…"
    : lastFinished
      ? lastFinished.status === "completed"
        ? `Sweep finished — ${lastFinished.proposals_emitted} proposal${
            lastFinished.proposals_emitted === 1 ? "" : "s"
          } from ${lastFinished.paths_scanned} paths.`
        : `Sweep failed${lastFinished.error ? `: ${lastFinished.error}` : "."}`
      : null;

  return (
    <Card padding="xs" rounding="md" border="solid" background="heavy">
      <div className="flex w-full flex-col gap-1 p-1">
        <ContentAction
          sizePreset="main-ui"
          variant="section"
          icon={SvgSliders}
          title="Scan the whole wiki"
          description="Run a detection sweep now to find cleanups."
          rightChildren={
            <Button
              type="button"
              size="sm"
              prominence="secondary"
              disabled={busy || disabled}
              onClick={() => void run()}
            >
              {busy ? "Starting…" : "Run a sweep"}
            </Button>
          }
        />
        {note && (
          <Text font="secondary-body" color="status-success-05">
            {note}
          </Text>
        )}
        {error && <InputErrorText type="error">{error}</InputErrorText>}
      </div>
    </Card>
  );
}

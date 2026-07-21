"use client";

import { useState } from "react";

import { Button, Card, Switch, Text } from "@onyx-ai/opal/components";
import { SvgSliders } from "@onyx-ai/opal/icons";
import { ContentAction, InputErrorText } from "@onyx-ai/opal/layouts";

import {
  type AutoOrganizeSchedule,
  triggerSweep,
  updateAutoOrganizeSettings,
  useAutoOrganizeSettings,
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
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await triggerSweep();
      setNote("Sweep started.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to start sweep");
    } finally {
      setBusy(false);
    }
  }

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

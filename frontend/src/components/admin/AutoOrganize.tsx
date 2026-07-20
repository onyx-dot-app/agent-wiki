"use client";

import { useState } from "react";

import { Button, Card, Switch, Text } from "@onyx-ai/opal/components";
import { SvgSliders } from "@onyx-ai/opal/icons";
import { ContentAction, InputErrorText } from "@onyx-ai/opal/layouts";

import {
  triggerSweep,
  updateAutoOrganizeEnabled,
  useAutoOrganizeSettings,
} from "@/lib/autoOrganize";

export function AutoOrganize() {
  const { settings, isLoading, error, refresh } = useAutoOrganizeSettings();
  const enabled = settings?.enabled ?? true;

  return (
    <div className="flex w-full flex-col gap-4">
      <Text font="main-ui-body" color="text-03">
        Run a detection sweep to find structural cleanups (such as empty
        folders). Cleanups in AI-managed scopes are applied automatically;
        others are proposed for review.
      </Text>
      <EnabledToggle
        enabled={enabled}
        loading={isLoading}
        loadError={error instanceof Error ? error.message : null}
        onChanged={() => void refresh()}
      />
      <SweepControl disabled={!enabled} />
    </div>
  );
}

interface EnabledToggleProps {
  enabled: boolean;
  loading: boolean;
  loadError: string | null;
  onChanged: () => void;
}

function EnabledToggle({
  enabled,
  loading,
  loadError,
  onChanged,
}: EnabledToggleProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const message = loadError || error;

  async function toggle(next: boolean) {
    setBusy(true);
    setError(null);
    try {
      await updateAutoOrganizeEnabled(next);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to update setting");
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
          title="Auto Organize"
          description="Master switch. When off, no sweeps run, nothing is auto-applied, and pending proposals are frozen — per-page settings are kept."
          rightChildren={
            <Switch
              checked={enabled}
              disabled={loading || busy}
              onCheckedChange={(next) => void toggle(next)}
            />
          }
        />
        {message && <InputErrorText type="error">{message}</InputErrorText>}
      </div>
    </Card>
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

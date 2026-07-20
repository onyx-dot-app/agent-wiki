"use client";

import { useState } from "react";

import { Button, Card, Text } from "@onyx-ai/opal/components";
import { SvgSliders } from "@onyx-ai/opal/icons";
import { ContentAction, InputErrorText } from "@onyx-ai/opal/layouts";

import { triggerSweep } from "@/lib/autoOrganize";

export function AutoOrganize() {
  return (
    <div className="flex w-full flex-col gap-4">
      <Text font="main-ui-body" color="text-03">
        Run a detection sweep to find structural cleanups (such as empty
        folders). Cleanups in AI-managed scopes are applied automatically;
        others are proposed for review.
      </Text>
      <SweepControl />
    </div>
  );
}

function SweepControl() {
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
              disabled={busy}
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

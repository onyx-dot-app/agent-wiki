"use client";

import { useState } from "react";

import { Button, Card, Text } from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";

import { invalidateHelperProbe, probeHelper } from "@/lib/launchers";

export function InstallHelperPane({
  onReprobe,
}: {
  onReprobe: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  const [manualBusy, setManualBusy] = useState(false);

  async function reprobe() {
    setBusy(true);
    try {
      invalidateHelperProbe();
      // Explicit user gesture — force the iframe probe even though
      // we don't yet have an ever-installed flag.
      await probeHelper({ force: true });
      await onReprobe();
    } finally {
      setBusy(false);
    }
  }

  async function manualTest() {
    setManualBusy(true);
    try {
      invalidateHelperProbe();
      const nonce = `n_${Math.random().toString(36).slice(2)}_${Date.now()}`;
      window.location.href = `agentwiki://probe?nonce=${encodeURIComponent(
        nonce,
      )}&endpoint=${encodeURIComponent(window.location.origin)}`;
      await Promise.resolve(onReprobe());
    } finally {
      setManualBusy(false);
    }
  }

  function download() {
    window.location.href = "/api/installer/app";
  }

  return (
    <Card padding="md" border="solid" borderColor="warning" rounding="sm">
      <Section
        flexDirection="column"
        alignItems="start"
        justifyContent="start"
        gap={0.75}
        width="full"
      >
        <Text font="secondary-body" color="text-04" as="p">
          Launcher isn&apos;t installed on this machine.
        </Text>
        <Button size="md" variant="action" onClick={download}>
          Download installer
        </Button>
        <Text font="secondary-body" color="text-04" as="p">
          Open the downloaded zip, drag AgentWikiLauncher.app to your
          Applications folder, then click Run Agent.
        </Text>
        <Section
          flexDirection="row"
          alignItems="center"
          justifyContent="end"
          gap={0.75}
          width="full"
        >
          <Button
            size="md"
            prominence="tertiary"
            onClick={manualTest}
            disabled={manualBusy}
          >
            Test launcher manually
          </Button>
          <Button size="md" variant="action" onClick={reprobe} disabled={busy}>
            {busy ? "Checking..." : "I've installed it"}
          </Button>
        </Section>
      </Section>
    </Card>
  );
}

"use client";

import { useEffect, useState } from "react";

import { Button, Text } from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";

import { probeHelper } from "@/lib/launchers";

import { InstallHelperPane } from "./InstallHelperPane";

interface Props {
  onDone: () => void;
  onCancel: () => void;
}

// Setup is a single step: install the one launcher helper. It's
// tool-agnostic — once installed it can launch any supported agent whose
// CLI is on PATH, so there's no per-tool setup to pick through.
export function SetupWizard({ onDone, onCancel }: Props) {
  const [acked, setAcked] = useState<boolean | null>(null);
  const [probing, setProbing] = useState(false);

  async function runProbe() {
    setProbing(true);
    try {
      const h = await probeHelper();
      setAcked(h.acked);
    } finally {
      setProbing(false);
    }
  }

  useEffect(() => {
    void runProbe();
  }, []);

  return (
    <Section
      flexDirection="column"
      alignItems="start"
      justifyContent="start"
      gap={1}
      width="full"
    >
      <Text font="main-ui-body" color="text-04" as="p">
        Set up the launcher
      </Text>
      <Text font="secondary-body" color="text-03" as="p">
        Install the launcher once to start agent sessions from any wiki page. It
        currently supports Claude Code and Codex.
      </Text>

      {acked ? (
        <Text font="secondary-body" color="text-03" as="p">
          ✓ Launcher detected on this machine.
        </Text>
      ) : (
        <InstallHelperPane onReprobe={runProbe} />
      )}

      <Section
        flexDirection="row"
        alignItems="center"
        justifyContent="end"
        gap={0.5}
        width="full"
      >
        <Button prominence="secondary" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="action" onClick={onDone} disabled={probing || !acked}>
          Done
        </Button>
      </Section>
    </Section>
  );
}

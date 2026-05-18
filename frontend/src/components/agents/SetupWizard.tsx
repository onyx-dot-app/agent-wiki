"use client";

import { useEffect, useState } from "react";

import { Button, Card, SelectCard, Tag, Text } from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";

import { probeHelper, type LauncherCatalogEntry } from "@/lib/launchers";

import { InstallHelperPane } from "./InstallHelperPane";

interface Props {
  catalog: LauncherCatalogEntry[];
  onDone: () => void;
  onCancel: () => void;
}

export function SetupWizard({ catalog, onDone, onCancel }: Props) {
  const [step, setStep] = useState<1 | 2>(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [helperState, setHelperState] = useState<{ acked: boolean } | null>(
    null,
  );
  const [probing, setProbing] = useState(false);

  async function runProbe() {
    setProbing(true);
    try {
      const h = await probeHelper();
      setHelperState({ acked: h.acked });
    } finally {
      setProbing(false);
    }
  }

  useEffect(() => {
    if (step === 2) void runProbe();
  }, [step]);

  return (
    <Section
      flexDirection="column"
      alignItems="start"
      justifyContent="start"
      gap={4}
      width="full"
    >
      {step === 1 && (
        <Step1
          catalog={catalog}
          selected={selected}
          onToggle={(id) => {
            const next = new Set(selected);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            setSelected(next);
          }}
          onCancel={onCancel}
          onNext={() => setStep(2)}
        />
      )}
      {step === 2 && (
        <Step2
          catalog={catalog.filter((c) => selected.has(c.id))}
          helperState={helperState}
          probing={probing}
          onReprobe={runProbe}
          onBack={() => setStep(1)}
          onDone={onDone}
        />
      )}
    </Section>
  );
}

function Step1({
  catalog,
  selected,
  onToggle,
  onCancel,
  onNext,
}: {
  catalog: LauncherCatalogEntry[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onCancel: () => void;
  onNext: () => void;
}) {
  return (
    <>
      <Text font="main-ui-body" color="text-04" as="p">
        Pick which tools to set up — step 1 of 2
      </Text>
      <Text font="secondary-body" color="text-03" as="p">
        You can add more later from the Agents page.
      </Text>
      <Section
        flexDirection="column"
        alignItems="start"
        justifyContent="start"
        gap={2}
        width="full"
      >
        {catalog.map((c) => (
          <SelectCard
            key={c.id}
            state={selected.has(c.id) ? "selected" : "empty"}
            onClick={() => onToggle(c.id)}
            padding="md"
            rounding="md"
            border="solid"
          >
            <Section
              flexDirection="row"
              alignItems="center"
              justifyContent="start"
              gap={2.5}
              width="full"
            >
              <img src={c.icon_url} alt="" width={20} height={20} />
              <Section
                flexDirection="column"
                alignItems="start"
                justifyContent="center"
                width="full"
                height="fit"
                gap={0.5}
              >
                <Text font="main-ui-body" color="text-04" nowrap>
                  {c.name}
                </Text>
                <Text font="secondary-body" color="text-03" nowrap>
                  {c.tagline}
                </Text>
              </Section>
              <Tag
                color="gray"
                size="sm"
                title={c.kind === "in_app" ? "in-app" : "terminal"}
              />
            </Section>
          </SelectCard>
        ))}
      </Section>
      <Section
        flexDirection="row"
        alignItems="center"
        justifyContent="end"
        gap={2}
        width="full"
      >
        <Button prominence="secondary" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="action"
          onClick={onNext}
          disabled={selected.size === 0}
        >
          Next
        </Button>
      </Section>
    </>
  );
}

function Step2({
  catalog,
  helperState,
  probing,
  onReprobe,
  onBack,
  onDone,
}: {
  catalog: LauncherCatalogEntry[];
  helperState: { acked: boolean } | null;
  probing: boolean;
  onReprobe: () => Promise<void>;
  onBack: () => void;
  onDone: () => void;
}) {
  const needsHelper = catalog.some((c) => c.kind === "local_cli");
  // Done enables on launcher detection alone. CLI-presence probe isn't
  // shipped in v1 — if claude/codex is missing at spawn time, the
  // helper exits cli_not_found and the wiki UI flips the session to
  // failed with a toast.
  const allOk = !probing && (!needsHelper || !!helperState?.acked);

  return (
    <>
      <Text font="main-ui-body" color="text-04" as="p">
        Setup checklist — step 2 of 2
      </Text>
      <Section
        flexDirection="column"
        alignItems="start"
        justifyContent="start"
        gap={3}
        width="full"
      >
        {catalog.map((c) => (
          <Card key={c.id} padding="md" border="solid" rounding="sm">
            <Section
              flexDirection="row"
              alignItems="center"
              justifyContent="start"
              gap={2}
              width="full"
            >
              <img src={c.icon_url} alt="" width={20} height={20} />
              <Text font="main-ui-body" color="text-04">
                {c.name}
              </Text>
            </Section>
            <Section
              flexDirection="column"
              gap={1}
              alignItems="start"
              justifyContent="start"
              width="full"
            >
              <Tag
                color={c.setup_status.token ? "green" : "amber"}
                size="sm"
                title={
                  c.setup_status.token
                    ? "Token ready"
                    : "Token will auto-mint on launch"
                }
              />
              {c.kind === "local_cli" && (
                <>
                  <Tag
                    color={helperState?.acked ? "green" : "amber"}
                    size="sm"
                    title={
                      helperState?.acked
                        ? "Launcher detected"
                        : "Launcher not installed"
                    }
                  />
                  <Tag
                    color="gray"
                    size="sm"
                    title={`Install the ${c.id} CLI before launching`}
                  />
                </>
              )}
            </Section>
          </Card>
        ))}
        {needsHelper && !helperState?.acked && (
          <InstallHelperPane onReprobe={onReprobe} />
        )}
      </Section>
      <Section
        flexDirection="row"
        alignItems="center"
        justifyContent="end"
        gap={2}
        width="full"
      >
        <Button prominence="secondary" onClick={onBack}>
          Back
        </Button>
        <Button variant="action" onClick={onDone} disabled={!allOk}>
          Done
        </Button>
      </Section>
    </>
  );
}

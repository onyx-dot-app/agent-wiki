"use client";

import { useEffect, useState } from "react";

import { Button, Card, SelectCard, Tag, Text } from "@onyx-ai/opal/components";

import { probeHelper, type LauncherCatalogEntry } from "@/lib/launchers";

import { InstallHelperPane } from "./InstallHelperPane";
import styles from "./SetupWizard.module.css";

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
    <div className={styles.wrapper}>
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
    </div>
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
      <ul className={styles.toolList}>
        {catalog.map((c) => (
          <li key={c.id}>
            <SelectCard
              state={selected.has(c.id) ? "selected" : "empty"}
              onClick={() => onToggle(c.id)}
              padding="md"
              rounding="md"
              border="solid"
            >
              <div className="flex items-center gap-2.5 w-full">
                <img src={c.icon_url} alt="" width={20} height={20} />
                <div className="flex flex-col min-w-0 flex-1">
                  <Text font="main-ui-body" color="text-04" nowrap>
                    {c.name}
                  </Text>
                  <Text font="secondary-body" color="text-03" nowrap>
                    {c.tagline}
                  </Text>
                </div>
                <Tag
                  color="gray"
                  size="sm"
                  title={c.kind === "in_app" ? "in-app" : "terminal"}
                />
              </div>
            </SelectCard>
          </li>
        ))}
      </ul>
      <div className={styles.actions}>
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
      </div>
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
      <div className={styles.checklist}>
        {catalog.map((c) => (
          <Card key={c.id} padding="md" border="solid" rounding="sm">
            <div className="flex items-center gap-2 mb-1.5">
              <img src={c.icon_url} alt="" width={20} height={20} />
              <Text font="main-ui-body" color="text-04">
                {c.name}
              </Text>
            </div>
            <div className="flex flex-col gap-1 items-start">
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
            </div>
          </Card>
        ))}
        {needsHelper && !helperState?.acked && (
          <InstallHelperPane onReprobe={onReprobe} />
        )}
      </div>
      <div className={styles.actions}>
        <Button prominence="secondary" onClick={onBack}>
          Back
        </Button>
        <Button variant="action" onClick={onDone} disabled={!allOk}>
          Done
        </Button>
      </div>
    </>
  );
}

"use client";

import { useEffect, useState } from "react";

import { Button } from "@onyx-ai/opal/components";
import {
  probeCli,
  probeHelper,
  type LauncherCatalogEntry,
} from "@/lib/launchers";

import { InstallHelperPane } from "./InstallHelperPane";
import { ToolStatusBadge } from "./ToolStatusBadge";
import styles from "./SetupWizard.module.css";

interface Props {
  catalog: LauncherCatalogEntry[];
  onDone: () => void;
  onCancel: () => void;
}

interface CliStatus {
  present: boolean;
  version: string | null;
  meets_min: boolean;
}

export function SetupWizard({ catalog, onDone, onCancel }: Props) {
  const [step, setStep] = useState<1 | 2>(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [helperState, setHelperState] = useState<{
    acked: boolean;
    port: number | null;
  } | null>(null);
  const [cliState, setCliState] = useState<Record<string, CliStatus> | null>(
    null,
  );
  const [probing, setProbing] = useState(false);

  async function runProbes() {
    setProbing(true);
    try {
      const h = await probeHelper();
      setHelperState({ acked: h.acked, port: h.helperPort });
      if (h.acked && h.helperPort && selected.size > 0) {
        const ids = Array.from(selected).filter(
          (id) => catalog.find((c) => c.id === id)?.kind === "local_cli",
        );
        if (ids.length > 0) {
          const c = await probeCli(h.helperPort, ids);
          setCliState(c);
        } else {
          setCliState({});
        }
      } else {
        setCliState({});
      }
    } finally {
      setProbing(false);
    }
  }

  useEffect(() => {
    if (step === 2) void runProbes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
          cliState={cliState}
          probing={probing}
          onReprobe={runProbes}
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
      <div className={styles.heading}>
        Pick which tools to set up — step 1 of 2
      </div>
      <div className={styles.subheading}>
        You can add more later from the Agents page.
      </div>
      <ul className={styles.toolList}>
        {catalog.map((c) => (
          <li key={c.id}>
            <label className={styles.toolOption}>
              <input
                type="checkbox"
                checked={selected.has(c.id)}
                onChange={() => onToggle(c.id)}
              />
              <img src={c.icon_url} alt="" width={20} height={20} />
              <div className={styles.toolOptionBody}>
                <div className={styles.toolOptionName}>{c.name}</div>
                <div className={styles.toolOptionTagline}>{c.tagline}</div>
              </div>
              <ToolStatusBadge
                status="muted"
                label={c.kind === "in_app" ? "in-app" : "terminal"}
              />
            </label>
          </li>
        ))}
      </ul>
      <div className={styles.actions}>
        <Button onClick={onCancel}>Cancel</Button>
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
  cliState,
  probing,
  onReprobe,
  onBack,
  onDone,
}: {
  catalog: LauncherCatalogEntry[];
  helperState: { acked: boolean; port: number | null } | null;
  cliState: Record<string, CliStatus> | null;
  probing: boolean;
  onReprobe: () => Promise<void>;
  onBack: () => void;
  onDone: () => void;
}) {
  const localCliTools = catalog.filter((c) => c.kind === "local_cli");
  const needsHelper = localCliTools.length > 0;
  const helperReady = !needsHelper || helperState?.acked === true;
  const cliReady =
    !needsHelper ||
    localCliTools.every((c) => cliState?.[c.id]?.meets_min === true);
  const allOk = !probing && helperReady && cliReady;

  return (
    <>
      <div className={styles.heading}>Setup checklist — step 2 of 2</div>
      <div className={styles.checklist}>
        {catalog.map((c) => (
          <div key={c.id} className={styles.checklistCard}>
            <div className={styles.checklistHeader}>
              <img src={c.icon_url} alt="" width={20} height={20} />
              <strong className={styles.checklistName}>{c.name}</strong>
            </div>
            <div className={styles.checklistBadges}>
              <ToolStatusBadge
                status={c.setup_status.token ? "ok" : "warn"}
                label={
                  c.setup_status.token
                    ? "Token ready"
                    : "Token will auto-mint on launch"
                }
              />
              {c.kind === "local_cli" && (
                <>
                  <ToolStatusBadge
                    status={helperState?.acked ? "ok" : "warn"}
                    label={
                      helperState?.acked
                        ? "Launcher detected"
                        : "Launcher not installed"
                    }
                  />
                  {helperState?.acked && cliState && (
                    <ToolStatusBadge
                      status={cliState[c.id]?.meets_min ? "ok" : "warn"}
                      label={
                        cliState[c.id]?.meets_min
                          ? `CLI ${cliState[c.id]?.version ?? ""} ready`
                          : `${c.id} not in PATH`
                      }
                    />
                  )}
                </>
              )}
            </div>
          </div>
        ))}
        {needsHelper && !helperState?.acked && (
          <InstallHelperPane onReprobe={onReprobe} />
        )}
      </div>
      <div className={styles.actions}>
        <Button onClick={onBack}>Back</Button>
        <Button variant="action" onClick={onDone} disabled={!allOk}>
          Done
        </Button>
      </div>
    </>
  );
}

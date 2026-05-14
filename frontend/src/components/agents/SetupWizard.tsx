"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/common/Button";
import {
  probeCli,
  probeHelper,
  type LauncherCatalogEntry,
} from "@/lib/launchers";
import { color, radius } from "@/lib/theme";

import { InstallHelperPane } from "./InstallHelperPane";
import { ToolStatusBadge } from "./ToolStatusBadge";

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
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
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
      <div
        style={{
          fontSize: 14,
          color: color.text.primary,
          fontWeight: 600,
        }}
      >
        Pick which tools to set up — step 1 of 2
      </div>
      <div style={{ fontSize: 12, color: color.text.muted }}>
        You can add more later from the Agents page.
      </div>
      <ul
        style={{
          listStyle: "none",
          padding: 0,
          margin: 0,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        {catalog.map((c) => (
          <li key={c.id}>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: 10,
                border: `1px solid ${color.border.default}`,
                borderRadius: radius.sm,
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={selected.has(c.id)}
                onChange={() => onToggle(c.id)}
              />
              <img src={c.icon_url} alt="" width={20} height={20} />
              <div style={{ flex: 1 }}>
                <div
                  style={{
                    fontSize: 14,
                    fontWeight: 600,
                    color: color.text.primary,
                  }}
                >
                  {c.name}
                </div>
                <div style={{ fontSize: 12, color: color.text.muted }}>
                  {c.tagline}
                </div>
              </div>
              <ToolStatusBadge
                status="muted"
                label={c.kind === "in_app" ? "in-app" : "terminal"}
              />
            </label>
          </li>
        ))}
      </ul>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <Button onClick={onCancel}>Cancel</Button>
        <Button
          variant="primary"
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
      <div
        style={{
          fontSize: 14,
          color: color.text.primary,
          fontWeight: 600,
        }}
      >
        Setup checklist — step 2 of 2
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {catalog.map((c) => (
          <div
            key={c.id}
            style={{
              padding: 12,
              border: `1px solid ${color.border.default}`,
              borderRadius: radius.sm,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 6,
              }}
            >
              <img src={c.icon_url} alt="" width={20} height={20} />
              <strong style={{ fontSize: 14 }}>{c.name}</strong>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
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
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <Button onClick={onBack}>Back</Button>
        <Button variant="primary" onClick={onDone} disabled={!allOk}>
          Done
        </Button>
      </div>
    </>
  );
}

"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";

import { SetupWizard } from "@/components/agents/SetupWizard";
import { ToolCard } from "@/components/agents/ToolCard";
import { WorkingDirInput } from "@/components/agents/WorkingDirInput";
import { Button, MessageCard, Text } from "@onyx-ai/opal/components";
import { Label, Section } from "@onyx-ai/opal/layouts";
import { ApiError } from "@/lib/api";
import {
  launch,
  probeHelper,
  useAgentSessions,
  useLauncherCatalog,
  type LauncherCatalogEntry,
} from "@/lib/launchers";

import styles from "./RunAgentModal.module.css";

interface Props {
  open: boolean;
  onClose: () => void;
  wikiPath: string | null;
}

interface ProbeState {
  acked: boolean;
  machineId: string | null;
}

export function RunAgentModal({ open, onClose, wikiPath }: Props) {
  const [probe, setProbe] = useState<ProbeState | null>(null);
  const { launchers, refresh: refreshCatalog } = useLauncherCatalog({
    machineId: probe?.machineId ?? null,
    wikiPath,
  });
  const { sessions, refresh: refreshSessions } = useAgentSessions(
    wikiPath ?? undefined,
  );
  const launchable = useMemo(
    () => launchers.filter((c) => c.available_for_launch),
    [launchers],
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [workingDir, setWorkingDir] = useState("");
  const [workdirEdited, setWorkdirEdited] = useState(false);
  const [rememberWorkdir, setRememberWorkdir] = useState(false);
  const [message, setMessage] = useState("");
  const [wizardOpen, setWizardOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function refreshProbe() {
    void probeHelper().then((r) =>
      setProbe({ acked: r.acked, machineId: r.machineId }),
    );
  }

  // Persist pending-launch state to sessionStorage in case the
  // browser navigates away to dispatch agentwiki:// and comes back.
  useEffect(() => {
    if (!open || !wikiPath) return;
    const key = `agentwiki:pending-launch:${wikiPath}`;
    const stashed = sessionStorage.getItem(key);
    if (stashed) {
      try {
        const s = JSON.parse(stashed) as {
          selectedId: string | null;
          workingDir: string;
          message: string;
        };
        if (typeof s.selectedId === "string") setSelectedId(s.selectedId);
        if (typeof s.workingDir === "string") {
          setWorkingDir(s.workingDir);
          setWorkdirEdited(true);
        }
        if (typeof s.message === "string") setMessage(s.message);
      } catch {
        sessionStorage.removeItem(key);
      }
    }
  }, [open, wikiPath]);

  useEffect(() => {
    if (!open) return;
    setError(null);
    refreshProbe();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    if (launchable.length === 0) {
      setSelectedId(null);
      setWorkdirEdited(false);
      return;
    }
    if (selectedId === null) {
      setSelectedId(launchable[0].id);
      setWorkdirEdited(false);
      return;
    }
    if (!launchable.some((c) => c.id === selectedId)) {
      setSelectedId(launchable[0].id);
      setWorkdirEdited(false);
    }
  }, [open, launchable, selectedId]);

  useEffect(() => {
    if (!open) return;
    if (workdirEdited) return;
    const entry = launchers.find((c) => c.id === selectedId);
    if (!entry) return;
    const next = entry.default_working_dir ?? "";
    if (workingDir !== next) {
      setWorkingDir(next);
    }
  }, [open, launchers, selectedId, workdirEdited, workingDir]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const handleWorkdirChange = (next: string) => {
    setWorkdirEdited(true);
    setWorkingDir(next);
  };

  async function onRun(e: FormEvent) {
    e.preventDefault();
    if (!selectedId) return;
    // Only local_cli tools require the localhost helper. in_app
    // (e.g. onyx-craft) and web_handoff tools launch through the
    // backend directly — gating them on the probe result would trap
    // the user in a wizard loop they can never escape.
    const selectedTool = launchable.find((c) => c.id === selectedId);
    if (selectedTool?.kind === "local_cli" && probe?.acked === false) {
      setWizardOpen(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // Stash before URI navigation so a bounce-back restores state.
      if (wikiPath) {
        sessionStorage.setItem(
          `agentwiki:pending-launch:${wikiPath}`,
          JSON.stringify({
            selectedId,
            workingDir,
            message,
          }),
        );
      }
      const res = await launch({
        tool_id: selectedId,
        wiki_path: wikiPath,
        working_dir: workingDir.trim() || null,
        message,
        machine_id: probe?.machineId ?? undefined,
        remember_workdir_for_page: rememberWorkdir,
      });
      window.location.href = res.uri;
      // Clear the stash now that the launch went through — leaving it
      // would pre-fill the next modal open for this page with the
      // previous message + workdir.
      if (wikiPath) {
        sessionStorage.removeItem(`agentwiki:pending-launch:${wikiPath}`);
      }
      onClose();
      await refreshSessions();
      await refreshCatalog();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to launch");
      setBusy(false);
    }
  }

  const canRun =
    message.trim().length > 0 &&
    selectedId !== null &&
    launchable.some((c) => c.id === selectedId);

  return (
    <div
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      className={styles.scrim}
    >
      <form
        onSubmit={onRun}
        role="dialog"
        aria-modal="true"
        aria-label="Run agent"
        className={styles.dialog}
      >
        <h2 className={styles.title}>Run agent</h2>

        {wizardOpen ? (
          <SetupWizard
            catalog={launchers}
            onDone={() => {
              setWizardOpen(false);
              refreshProbe();
              void refreshCatalog();
            }}
            onCancel={() => setWizardOpen(false)}
          />
        ) : (
          <>
            <ToolList
              catalog={launchable}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />

            <WorkingDirInput
              value={workingDir}
              onChange={handleWorkdirChange}
              remember={rememberWorkdir}
              onRememberChange={setRememberWorkdir}
              pageHasBinding={
                !!launchers.find((c) => c.id === selectedId)
                  ?.default_working_dir
              }
            />

            <Section
              flexDirection="column"
              alignItems="start"
              justifyContent="start"
              gap={1}
              width="full"
            >
              <Label label="run-agent-message">
                <Text font="secondary-action" color="text-04">
                  Message
                </Text>
              </Label>
              <textarea
                id="run-agent-message"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="What should the agent do with this doc?"
                rows={4}
                maxLength={16_384}
                className={styles.textarea}
              />
            </Section>

            {(() => {
              const live = sessions.filter(
                (s) => s.status === "active" || s.status === "idle",
              );
              if (live.length === 0) return null;
              return (
                <Section
                  flexDirection="column"
                  alignItems="start"
                  justifyContent="start"
                  gap={0.5}
                  width="full"
                >
                  <Text font="secondary-action" color="text-04">
                    Active sessions on this page
                  </Text>
                  {live.map((s) => (
                    <Text
                      key={s.id}
                      font="secondary-body"
                      color="text-03"
                      nowrap
                    >
                      {`${s.tool_id} · ${s.status} · ${s.started_at}`}
                    </Text>
                  ))}
                </Section>
              );
            })()}

            {error && <MessageCard variant="error" title={error} />}

            <Section
              flexDirection="row"
              justifyContent="between"
              alignItems="center"
              width="full"
            >
              <Button
                prominence="tertiary"
                size="sm"
                onClick={() => setWizardOpen(true)}
              >
                Set up another tool →
              </Button>
              <Section
                flexDirection="row"
                justifyContent="end"
                alignItems="center"
                width="fit"
                gap={1}
              >
                <Button type="button" prominence="secondary" onClick={onClose}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="action"
                  disabled={!canRun || busy}
                >
                  {busy ? "Launching..." : "Run"}
                </Button>
              </Section>
            </Section>
          </>
        )}
      </form>
    </div>
  );
}

function ToolList({
  catalog,
  selectedId,
  onSelect,
}: {
  catalog: LauncherCatalogEntry[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (catalog.length === 0) {
    return (
      <Text font="secondary-body" color="text-03">
        No launchable tools available yet.
      </Text>
    );
  }
  return (
    <Section
      flexDirection="column"
      alignItems="start"
      justifyContent="start"
      gap={1}
      width="full"
    >
      <Text font="secondary-action" color="text-04">
        Tool
      </Text>
      <Section
        flexDirection="column"
        alignItems="start"
        justifyContent="start"
        gap={1}
        width="full"
      >
        {catalog.map((c) => (
          <ToolCard
            key={c.id}
            name={c.name}
            tagline={c.tagline}
            iconUrl={c.icon_url}
            selected={c.id === selectedId}
            onSelect={() => onSelect(c.id)}
          />
        ))}
      </Section>
    </Section>
  );
}

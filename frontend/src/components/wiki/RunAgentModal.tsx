"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";

import { SetupWizard } from "@/components/agents/SetupWizard";
import { ToolCard } from "@/components/agents/ToolCard";
import { WorkingDirInput } from "@/components/agents/WorkingDirInput";
import { Button } from "@onyx-ai/opal/components";
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
    if (probe?.acked === false) {
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
              helperAcked={probe?.acked ?? null}
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

            <label className={styles.field}>
              <span className={styles.fieldLabel}>Message</span>
              <textarea
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="What should the agent do with this doc?"
                rows={4}
                maxLength={16_384}
                className={styles.textarea}
              />
            </label>

            {sessions.length > 0 && (
              <div className={styles.sessions}>
                <div className={styles.sessionsHeader}>
                  Active sessions on this page
                </div>
                <ul className={styles.sessionsList}>
                  {sessions.map((s) => (
                    <li key={s.id} className={styles.sessionsRow}>
                      {s.tool_id} · {s.status} · {s.started_at}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {error && <div className={styles.error}>{error}</div>}

            <div className={styles.footer}>
              <button
                type="button"
                onClick={() => setWizardOpen(true)}
                className={styles.linkButton}
              >
                Set up another tool →
              </button>
              <div className={styles.footerActions}>
                <Button type="button" onClick={onClose}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="action"
                  disabled={!canRun || busy}
                >
                  {busy ? "Launching..." : "Run"}
                </Button>
              </div>
            </div>
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
  helperAcked,
}: {
  catalog: LauncherCatalogEntry[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  helperAcked: boolean | null;
}) {
  if (catalog.length === 0) {
    return (
      <div className={styles.toolListEmpty}>
        No launchable tools available yet.
      </div>
    );
  }
  return (
    <div className={styles.toolList}>
      <span className={styles.fieldLabel}>Tool</span>
      <ul className={styles.toolListItems}>
        {catalog.map((c) => (
          <li key={c.id}>
            <ToolCard
              id={c.id}
              name={c.name}
              tagline={c.tagline}
              iconUrl={c.icon_url}
              selected={c.id === selectedId}
              onSelect={() => onSelect(c.id)}
              tokenReady={c.setup_status.token}
              helperReady={c.kind === "in_app" || helperAcked === true}
              cliReady={
                c.kind === "in_app"
                  ? true
                  : helperAcked === false
                    ? false
                    : null
              }
            />
          </li>
        ))}
      </ul>
    </div>
  );
}

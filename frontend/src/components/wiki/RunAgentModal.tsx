"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";

import { SetupWizard } from "@/components/agents/SetupWizard";
import { ToolCard } from "@/components/agents/ToolCard";
import { WorkingDirInput } from "@/components/agents/WorkingDirInput";
import { Button } from "@/components/common/Button";
import { ApiError } from "@/lib/api";
import {
  launch,
  probeHelper,
  useAgentSessions,
  useLauncherCatalog,
  type LauncherCatalogEntry,
} from "@/lib/launchers";
import { color, radius, shadow } from "@/lib/theme";

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

  // R7#2 — persist pending-launch state to sessionStorage in case the
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
      // R7#2 — stash before URI navigation so a bounce-back restores state.
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
      style={{
        position: "fixed",
        inset: 0,
        background: color.overlay,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <form
        onSubmit={onRun}
        role="dialog"
        aria-modal="true"
        aria-label="Run agent"
        style={{
          background: color.bg.page,
          borderRadius: radius.lg,
          width: "min(560px, 92vw)",
          padding: 22,
          boxShadow: shadow.modal,
          display: "flex",
          flexDirection: "column",
          gap: 14,
          maxHeight: "90vh",
          overflowY: "auto",
        }}
      >
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Run agent</h2>

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

            <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span
                style={{
                  fontSize: 12,
                  color: color.text.secondary,
                  fontWeight: 600,
                }}
              >
                Message
              </span>
              <textarea
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="What should the agent do with this doc?"
                rows={4}
                maxLength={16_384}
                style={{
                  padding: 10,
                  border: `1px solid ${color.border.default}`,
                  borderRadius: radius.md,
                  fontFamily: "inherit",
                  fontSize: 14,
                  lineHeight: 1.5,
                  resize: "vertical",
                  minHeight: 96,
                  color: color.text.primary,
                  background: color.bg.page,
                }}
              />
            </label>

            {sessions.length > 0 && (
              <div>
                <div
                  style={{
                    fontSize: 12,
                    color: color.text.secondary,
                    fontWeight: 600,
                    marginBottom: 4,
                  }}
                >
                  Active sessions on this page
                </div>
                <ul
                  style={{
                    listStyle: "none",
                    padding: 0,
                    margin: 0,
                    fontSize: 13,
                  }}
                >
                  {sessions.map((s) => (
                    <li key={s.id} style={{ color: color.text.muted }}>
                      {s.tool_id} · {s.status} · {s.started_at}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {error && (
              <div
                style={{
                  padding: 8,
                  background: color.state.danger.bg,
                  color: color.state.danger.fg,
                  borderRadius: radius.sm,
                  fontSize: 13,
                }}
              >
                {error}
              </div>
            )}

            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginTop: 4,
              }}
            >
              <button
                type="button"
                onClick={() => setWizardOpen(true)}
                style={{
                  background: "transparent",
                  border: "none",
                  color: color.text.muted,
                  fontSize: 12,
                  cursor: "pointer",
                  padding: 0,
                }}
              >
                Set up another tool →
              </button>
              <div style={{ display: "flex", gap: 8 }}>
                <Button type="button" onClick={onClose}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="primary"
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
      <div style={{ fontSize: 13, color: color.text.muted }}>
        No launchable tools available yet.
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span
        style={{
          fontSize: 12,
          color: color.text.secondary,
          fontWeight: 600,
        }}
      >
        Tool
      </span>
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

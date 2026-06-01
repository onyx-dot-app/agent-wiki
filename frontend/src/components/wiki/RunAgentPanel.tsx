"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";

import { SetupWizard } from "@/components/agents/SetupWizard";
import { ToolCard } from "@/components/agents/ToolCard";
import { WorkingDirInput } from "@/components/agents/WorkingDirInput";
import { Button, MessageCard, Text } from "@onyx-ai/opal/components";
import { SvgChevronDown, SvgFileText, SvgX } from "@onyx-ai/opal/icons";
import { SvgClaude, SvgOnyxLogo, SvgOpenai } from "@onyx-ai/opal/logos";
import { ApiError } from "@/lib/api";
import {
  launch,
  probeHelper,
  useAgentSessions,
  useLauncherCatalog,
} from "@/lib/launchers";

import styles from "./RunAgentPanel.module.css";

interface Props {
  open: boolean;
  onClose: () => void;
  wikiPath: string | null;
}

interface ProbeState {
  acked: boolean;
  machineId: string | null;
}

function agentIcon(id: string | null) {
  if (!id) return undefined;
  if (id.includes("claude")) return SvgClaude;
  if (id.includes("codex") || id.includes("openai")) return SvgOpenai;
  if (id.includes("craft") || id.includes("onyx")) return SvgOnyxLogo;
  return undefined;
}

function docName(wikiPath: string | null): string | null {
  if (!wikiPath) return null;
  const base = wikiPath.split("/").pop() ?? wikiPath;
  return base.replace(/\.md$/, "");
}

export function RunAgentPanel({ open, onClose, wikiPath }: Props) {
  const [probe, setProbe] = useState<ProbeState | null>(null);
  const { launchers, refresh: refreshCatalog } = useLauncherCatalog({
    machineId: probe?.machineId ?? null,
    wikiPath,
  });
  const { refresh: refreshSessions } = useAgentSessions(wikiPath ?? undefined);
  const launchable = useMemo(
    () => launchers.filter((c) => c.available_for_launch),
    [launchers],
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [docContextOn, setDocContextOn] = useState(true);
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
    // Reset transient state on (re)open: a successful launch dispatches the
    // agentwiki:// URI without navigating away, so busy/pickerOpen would
    // otherwise stay stuck from the previous open.
    setError(null);
    setBusy(false);
    setPickerOpen(false);
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

  const selected = launchers.find((c) => c.id === selectedId);
  const selectedName = selected?.name ?? "your agent";
  const selectedKind = selected?.kind;
  const docLabel = docName(wikiPath);
  const SelectedIcon = agentIcon(selectedId);

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
        // Removing the doc-context chip launches without page context.
        wiki_path: docContextOn ? wikiPath : null,
        working_dir: workingDir.trim() || null,
        message,
        machine_id: probe?.machineId ?? undefined,
        remember_workdir_for_page: rememberWorkdir,
      });
      window.location.href = res.uri;
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
    <form
      onSubmit={onRun}
      role="dialog"
      aria-modal={false}
      aria-label="Quick Launch Agent"
      className={styles.panel}
    >
      <div className={styles.headerBand}>
        <div className={styles.headerText}>
          <Text font="main-content-emphasis" color="text-04">
            Quick Launch Agent
          </Text>
          <Text font="secondary-body" color="text-03">
            Start a session with your external agents
          </Text>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className={styles.closeBtn}
        >
          <SvgX />
        </button>
      </div>

      {wizardOpen ? (
        <div className={styles.contentBand}>
          <SetupWizard
            onDone={() => {
              setWizardOpen(false);
              refreshProbe();
              void refreshCatalog();
            }}
            onCancel={() => setWizardOpen(false)}
          />
        </div>
      ) : (
        <>
          <div className={styles.contentBand}>
            <div className={styles.section}>
              <Text font="main-ui-action" color="text-04">
                Agent to Launch
              </Text>
              {launchable.length === 0 ? (
                <Text font="secondary-body" color="text-03">
                  No launchable agents available yet.
                </Text>
              ) : (
                <>
                  <button
                    type="button"
                    className={styles.select}
                    onClick={() => setPickerOpen((o) => !o)}
                  >
                    {SelectedIcon && (
                      <span className={styles.selectIcon}>
                        <SelectedIcon />
                      </span>
                    )}
                    <span className={styles.selectName}>{selectedName}</span>
                    <span className={styles.selectChevron}>
                      <SvgChevronDown />
                    </span>
                  </button>
                  {pickerOpen && launchable.length > 1 && (
                    <div className={styles.picker}>
                      {launchable.map((c) => (
                        <ToolCard
                          key={c.id}
                          toolId={c.id}
                          name={c.name}
                          tagline={c.tagline}
                          selected={c.id === selectedId}
                          onSelect={() => {
                            setSelectedId(c.id);
                            setPickerOpen(false);
                          }}
                        />
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>

            {selectedKind === "local_cli" && (
              <>
                <WorkingDirInput
                  value={workingDir}
                  onChange={handleWorkdirChange}
                  remember={rememberWorkdir}
                  onRememberChange={setRememberWorkdir}
                  pageHasBinding={!!selected?.default_working_dir}
                />

                {(() => {
                  const warning = selected?.unscoped_workdir_warning;
                  if (!warning || workingDir.trim().length > 0) return null;
                  const body = warning.replace(
                    /^No directory set\s*[—-]\s*/,
                    "",
                  );
                  return (
                    <MessageCard
                      variant="warning"
                      title="No directory set"
                      description={body}
                    />
                  );
                })()}
              </>
            )}

            <div className={styles.divider} />

            <div className={styles.section}>
              <Text font="main-ui-action" color="text-04">
                Message
              </Text>
              <div className={styles.taginput}>
                <div className={styles.taginputBody}>
                  {docContextOn && docLabel && (
                    <span className={styles.chip}>
                      <SvgFileText />
                      <span className={styles.chipLabel}>{docLabel}</span>
                      <button
                        type="button"
                        className={styles.chipClose}
                        aria-label="Remove page context"
                        onClick={() => setDocContextOn(false)}
                      >
                        <SvgX />
                      </button>
                    </span>
                  )}
                  <textarea
                    id="run-agent-message"
                    className={styles.taginputArea}
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    placeholder="Add more details and tasks for the agent"
                    rows={3}
                    maxLength={16_384}
                  />
                </div>
              </div>
            </div>

            {error && <MessageCard variant="error" title={error} />}
          </div>

          <div className={styles.footerBand}>
            <span className={styles.helper}>
              This will launch a session in <strong>{selectedName}</strong> with
              your message.
            </span>
            <Button type="submit" variant="action" disabled={!canRun || busy}>
              {busy ? "Launching..." : "Start"}
            </Button>
          </div>
        </>
      )}
    </form>
  );
}

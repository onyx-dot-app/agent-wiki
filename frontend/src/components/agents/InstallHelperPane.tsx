"use client";

import { useState } from "react";

import { Button } from "@onyx-ai/opal/components";

import { invalidateHelperProbe } from "@/lib/launchers";

import styles from "./InstallHelperPane.module.css";

const INSTALL_CMD = "npm install -g @onyx-ai/agentwiki-launcher";

export function InstallHelperPane({
  onReprobe,
}: {
  onReprobe: () => Promise<void> | void;
}) {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [manualBusy, setManualBusy] = useState(false);

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Some browsers block clipboard outside HTTPS — show feedback anyway.
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  async function reprobe() {
    setBusy(true);
    try {
      invalidateHelperProbe();
      await onReprobe();
    } finally {
      setBusy(false);
    }
  }

  async function manualTest() {
    // User-gesture top-level navigation works even when iframe
    // probe is blocked. After dispatch the user returns to the page;
    // we kick a fresh probe.
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

  return (
    <div className={styles.pane}>
      <div className={styles.message}>
        Launcher isn&apos;t installed on this machine. Run:
      </div>
      <div className={styles.cmdRow}>
        <code className={styles.cmd}>{INSTALL_CMD}</code>
        <Button size="md" prominence="secondary" onClick={() => copy(INSTALL_CMD)}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <div className={styles.actions}>
        <Button size="md" prominence="tertiary" onClick={manualTest} disabled={manualBusy}>
          Test launcher manually
        </Button>
        <Button size="md" variant="action" onClick={reprobe} disabled={busy}>
          {busy ? "Checking..." : "I've installed it"}
        </Button>
      </div>
    </div>
  );
}

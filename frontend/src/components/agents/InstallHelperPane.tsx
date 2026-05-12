"use client";

import { useState } from "react";

import { Button } from "@/components/common/Button";
import { invalidateHelperProbe } from "@/lib/launchers";
import { color, radius } from "@/lib/theme";

const INSTALL_CMD = "npm install -g @agentwiki/launcher";

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
    // AF#9 — user-gesture top-level navigation works even when iframe
    // probe is blocked. After dispatch the user returns to the page;
    // we kick a fresh probe.
    setManualBusy(true);
    try {
      invalidateHelperProbe();
      const nonce = `n_${Math.random().toString(36).slice(2)}_${Date.now()}`;
      window.location.href = `agentwiki://probe?nonce=${encodeURIComponent(
        nonce,
      )}&endpoint=${encodeURIComponent(window.location.origin)}`;
    } finally {
      setManualBusy(false);
    }
  }

  return (
    <div
      style={{
        padding: 12,
        background: color.state.warning.bg,
        border: `1px solid ${color.state.warning.border}`,
        borderRadius: radius.sm,
      }}
    >
      <div
        style={{
          fontSize: 13,
          color: color.state.warning.fg,
          marginBottom: 8,
        }}
      >
        Launcher isn&apos;t installed on this machine. Run:
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <code
          style={{
            flex: 1,
            padding: "6px 8px",
            background: color.bg.sunken,
            borderRadius: radius.xs,
            fontFamily: "ui-monospace, Menlo, monospace",
            fontSize: 12,
            color: color.text.primary,
          }}
        >
          {INSTALL_CMD}
        </code>
        <Button size="sm" onClick={() => copy(INSTALL_CMD)}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <div
        style={{
          marginTop: 10,
          display: "flex",
          gap: 8,
          justifyContent: "flex-end",
        }}
      >
        <Button
          size="sm"
          variant="ghost"
          onClick={manualTest}
          disabled={manualBusy}
        >
          Test launcher manually
        </Button>
        <Button size="sm" variant="primary" onClick={reprobe} disabled={busy}>
          {busy ? "Checking..." : "I've installed it"}
        </Button>
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";

import {
  formatScopePath,
  getTriggerHistory,
  type Trigger,
  type TriggerCommit,
} from "@/lib/triggers";
import { color, radius, shadow } from "@/lib/theme";

interface Props {
  trigger: Trigger | null;
  onClose: () => void;
  onSelectVersion: (sha: string) => void;
}

function formatTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function TriggerHistoryModal({ trigger, onClose, onSelectVersion }: Props) {
  const [commits, setCommits] = useState<TriggerCommit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!trigger) {
      setCommits([]);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    getTriggerHistory(trigger.id)
      .then((rows) => setCommits(rows))
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load history"))
      .finally(() => setLoading(false));
  }, [trigger]);

  if (!trigger) return null;

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
      <div
        style={{
          background: color.bg.page,
          borderRadius: radius.lg,
          width: "min(640px, 92vw)",
          maxHeight: "92vh",
          overflowY: "auto",
          padding: 24,
          boxShadow: shadow.modal,
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>Edit history</h2>
            <div
              title={trigger.scope_path}
              style={{
                fontFamily: "ui-monospace, Menlo, monospace",
                fontSize: 12,
                color: color.text.muted,
                marginTop: 4,
              }}
            >
              {formatScopePath(trigger.scope_path)}
            </div>
          </div>
          <button onClick={onClose} style={closeBtn} aria-label="Close">
            ×
          </button>
        </div>

        <p style={{ margin: 0, fontSize: 12, color: color.text.muted, lineHeight: 1.5 }}>
          Click a version to open it in the editor. Saving from there creates a
          new commit. Trigger <em>fires</em> live on the Events tab.
        </p>

        {loading && <div style={{ fontSize: 13, color: color.text.muted }}>Loading…</div>}

        {error && (
          <div
            style={{
              padding: 10,
              background: color.state.danger.bg,
              color: color.state.danger.fg,
              borderRadius: radius.sm,
              fontSize: 13,
            }}
          >
            {error}
          </div>
        )}

        {!loading && !error && commits.length === 0 && (
          <div style={{ fontSize: 13, color: color.text.muted }}>No history yet.</div>
        )}

        {commits.length > 0 && (
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {commits.map((c) => (
              <li key={c.sha} style={{ marginBottom: 6 }}>
                <button
                  type="button"
                  onClick={() => onSelectVersion(c.sha)}
                  style={rowBtn}
                >
                  <span style={{ fontSize: 13, color: color.text.primary }}>{formatTs(c.ts)}</span>
                  <span style={{ fontSize: 12, color: color.text.muted }}>{c.author}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

const closeBtn: React.CSSProperties = {
  background: "transparent",
  border: "none",
  fontSize: 22,
  cursor: "pointer",
  color: color.text.muted,
  lineHeight: 1,
  padding: 4,
};

const rowBtn: React.CSSProperties = {
  width: "100%",
  textAlign: "left",
  padding: "10px 12px",
  border: `1px solid ${color.border.default}`,
  borderRadius: radius.md,
  background: color.bg.panel,
  cursor: "pointer",
  display: "flex",
  alignItems: "baseline",
  justifyContent: "space-between",
  gap: 12,
  font: "inherit",
};

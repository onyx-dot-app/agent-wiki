"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/common/Button";
import { color, radius, shadow } from "@/lib/theme";

interface Props {
  open: boolean;
  onClose: () => void;
}

const AGENTS: { id: string; name: string; tagline: string }[] = [
  { id: "onyx-craft", name: "Onyx Craft", tagline: "In-app agent that drafts and edits docs." },
  { id: "claude-code", name: "Claude Code", tagline: "Coding agent — runs in the user's terminal." },
];

export function RunAgentModal({ open, onClose }: Props) {
  const [selected, setSelected] = useState<string>(AGENTS[0].id);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!open) return;
    setSelected(AGENTS[0].id);
    setMessage("");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    // No-op for now — wiring the actual agent dispatch is a follow-up.
  }

  const canRun = message.trim().length > 0;

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
        onSubmit={onSubmit}
        role="dialog"
        aria-modal="true"
        aria-label="Run agent"
        style={{
          background: color.bg.page,
          borderRadius: radius.lg,
          width: "min(520px, 92vw)",
          padding: 22,
          boxShadow: shadow.modal,
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: color.text.primary }}>Run agent</h2>
        <p style={{ margin: 0, fontSize: 13, color: color.text.muted }}>
          Pick an agent and write the message to send along with this document.
        </p>

        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={labelStyle}>Agent</span>
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 6 }}>
            {AGENTS.map((a) => (
              <li key={a.id}>
                <AgentOption
                  name={a.name}
                  tagline={a.tagline}
                  selected={selected === a.id}
                  onSelect={() => setSelected(a.id)}
                />
              </li>
            ))}
          </ul>
        </div>

        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={labelStyle}>Message</span>
          <textarea
            autoFocus
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="What should the agent do with this doc?"
            rows={4}
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

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={!canRun}
            title="Coming Soon!"
            onMouseDown={(e) => {
              if (!canRun) return;
              e.currentTarget.style.transform = "scale(0.97)";
              e.currentTarget.style.background = color.accent.bgHover;
            }}
            onMouseUp={(e) => {
              if (!canRun) return;
              e.currentTarget.style.transform = "scale(1)";
              e.currentTarget.style.background = color.accent.bg;
            }}
          >
            Run
          </Button>
        </div>
      </form>
    </div>
  );
}

function AgentOption({
  name,
  tagline,
  selected,
  onSelect,
}: {
  name: string;
  tagline: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        textAlign: "left",
        width: "100%",
        padding: "10px 12px",
        background: selected ? color.accent.subtleBg : color.bg.page,
        border: `1px solid ${selected ? color.accent.bg : color.border.default}`,
        borderRadius: radius.md,
        cursor: "pointer",
        transition: "transform 80ms ease, background 80ms ease, border-color 80ms ease",
      }}
      onMouseEnter={(e) => {
        if (!selected) e.currentTarget.style.background = color.bg.sunken;
      }}
      onMouseLeave={(e) => {
        if (!selected) e.currentTarget.style.background = color.bg.page;
      }}
      onMouseDown={(e) => {
        e.currentTarget.style.transform = "scale(0.98)";
      }}
      onMouseUp={(e) => {
        e.currentTarget.style.transform = "scale(1)";
      }}
    >
      <span style={{ fontSize: 14, fontWeight: 600, color: color.text.primary }}>
        {name}
      </span>
      <span style={{ fontSize: 12, color: color.text.muted, marginTop: 2 }}>{tagline}</span>
    </button>
  );
}

const labelStyle: React.CSSProperties = {
  fontSize: 12,
  fontWeight: 600,
  color: color.text.secondary,
  textTransform: "uppercase",
  letterSpacing: "0.04em",
};

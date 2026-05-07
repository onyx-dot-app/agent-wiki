"use client";

import { useEffect, useState, type FormEvent } from "react";

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
        background: "rgba(15,23,42,0.45)",
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
          background: "white",
          borderRadius: 12,
          width: "min(520px, 92vw)",
          padding: 22,
          boxShadow: "0 24px 60px rgba(0,0,0,0.18)",
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Run agent</h2>
        <p style={{ margin: 0, fontSize: 13, color: "#6b7280" }}>
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
              border: "1px solid #ddd",
              borderRadius: 8,
              fontFamily: "inherit",
              fontSize: 14,
              lineHeight: 1.5,
              resize: "vertical",
              minHeight: 96,
            }}
          />
        </label>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: "8px 14px",
              background: "transparent",
              border: "1px solid #ddd",
              borderRadius: 8,
              color: "#374151",
              cursor: "pointer",
              fontWeight: 600,
              fontSize: 13,
            }}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canRun}
            style={{
              padding: "8px 14px",
              background: "#6366f1",
              color: "white",
              border: "none",
              borderRadius: 8,
              cursor: canRun ? "pointer" : "not-allowed",
              fontWeight: 600,
              fontSize: 13,
              opacity: canRun ? 1 : 0.5,
              transition: "transform 80ms ease, background 80ms ease",
            }}
            onMouseDown={(e) => {
              if (!canRun) return;
              e.currentTarget.style.transform = "scale(0.97)";
              e.currentTarget.style.background = "#4f46e5";
            }}
            onMouseUp={(e) => {
              if (!canRun) return;
              e.currentTarget.style.transform = "scale(1)";
              e.currentTarget.style.background = "#6366f1";
            }}
          >
            Run
          </button>
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
        background: selected ? "#eef2ff" : "white",
        border: `1px solid ${selected ? "#6366f1" : "#e5e7eb"}`,
        borderRadius: 8,
        cursor: "pointer",
        transition: "transform 80ms ease, background 80ms ease, border-color 80ms ease",
      }}
      onMouseEnter={(e) => {
        if (!selected) e.currentTarget.style.background = "#f9fafb";
      }}
      onMouseLeave={(e) => {
        if (!selected) e.currentTarget.style.background = "white";
      }}
      onMouseDown={(e) => {
        e.currentTarget.style.transform = "scale(0.98)";
      }}
      onMouseUp={(e) => {
        e.currentTarget.style.transform = "scale(1)";
      }}
    >
      <span style={{ fontSize: 14, fontWeight: 600, color: selected ? "#3730a3" : "#111" }}>
        {name}
      </span>
      <span style={{ fontSize: 12, color: "#6b7280", marginTop: 2 }}>{tagline}</span>
    </button>
  );
}

const labelStyle: React.CSSProperties = {
  fontSize: 12,
  fontWeight: 600,
  color: "#374151",
  textTransform: "uppercase",
  letterSpacing: "0.04em",
};

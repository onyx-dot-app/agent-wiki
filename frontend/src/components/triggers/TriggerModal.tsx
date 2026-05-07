"use client";

import { useEffect, useState, type FormEvent } from "react";

import {
  createTrigger,
  updateTrigger,
  type Trigger,
  type TriggerCreateInput,
} from "@/lib/triggers";

interface Props {
  open: boolean;
  initial?: Partial<Trigger>;
  onClose: () => void;
  onSaved: (t: Trigger) => void;
  /** Lock the scope_path input so callers (e.g. doc page) can pin it. */
  lockScope?: boolean;
}

export function TriggerModal({ open, initial, onClose, onSaved, lockScope }: Props) {
  const isEdit = Boolean(initial?.id);
  const [scopePath, setScopePath] = useState(initial?.scope_path ?? "");
  const [description, setDescription] = useState(initial?.nl_description ?? "");
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setScopePath(initial?.scope_path ?? "");
    setDescription(initial?.nl_description ?? "");
    setEnabled(initial?.enabled ?? true);
    setError(null);
  }, [open, initial?.id, initial?.scope_path, initial?.nl_description, initial?.enabled]);

  if (!open) return null;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      let saved: Trigger;
      if (isEdit && initial?.id) {
        saved = await updateTrigger(initial.id, {
          scope_path: scopePath.trim(),
          nl_description: description.trim(),
          enabled,
        });
      } else {
        const input: TriggerCreateInput = {
          scope_path: scopePath.trim(),
          nl_description: description.trim(),
          enabled,
        };
        saved = await createTrigger(input);
      }
      onSaved(saved);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setBusy(false);
    }
  }

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
        style={{
          background: "white",
          borderRadius: 12,
          width: "min(560px, 92vw)",
          padding: 24,
          boxShadow: "0 24px 60px rgba(0,0,0,0.18)",
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>
          {isEdit ? "Edit trigger" : "New trigger"}
        </h2>

        <label style={labelStyle}>
          <span>Scope path</span>
          <input
            value={scopePath}
            onChange={(e) => setScopePath(e.target.value)}
            disabled={busy || lockScope}
            placeholder="projects/foo.md or projects"
            style={inputStyle}
          />
          <span style={hintStyle}>
            File path = file-scoped. Directory = matches every doc inside it.
          </span>
        </label>

        <label style={labelStyle}>
          <span>Fire when…</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={busy}
            rows={4}
            placeholder="e.g. status flips from green to yellow"
            style={{ ...inputStyle, fontFamily: "inherit", resize: "vertical" }}
          />
        </label>

        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            disabled={busy}
          />
          Enabled
        </label>

        {error && (
          <div
            style={{
              background: "#fef2f2",
              color: "#991b1b",
              borderRadius: 6,
              padding: 10,
              fontSize: 13,
            }}
          >
            {error}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button type="button" onClick={onClose} disabled={busy} style={secondaryBtn}>
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy || !scopePath.trim() || !description.trim()}
            style={{
              ...primaryBtn,
              opacity: busy || !scopePath.trim() || !description.trim() ? 0.6 : 1,
            }}
          >
            {busy ? "Saving…" : isEdit ? "Save" : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  fontSize: 13,
  color: "#374151",
};

const hintStyle: React.CSSProperties = { fontSize: 11, color: "#6b7280" };

const inputStyle: React.CSSProperties = {
  padding: 10,
  border: "1px solid #d1d5db",
  borderRadius: 6,
  fontSize: 14,
  outline: "none",
};

const primaryBtn: React.CSSProperties = {
  padding: "8px 14px",
  background: "#6366f1",
  color: "white",
  border: "none",
  borderRadius: 8,
  cursor: "pointer",
  fontWeight: 600,
  fontSize: 13,
};

const secondaryBtn: React.CSSProperties = {
  padding: "8px 14px",
  background: "transparent",
  color: "#374151",
  border: "1px solid #ddd",
  borderRadius: 8,
  cursor: "pointer",
  fontWeight: 600,
  fontSize: 13,
};

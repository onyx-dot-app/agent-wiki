"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import {
  createTrigger,
  getTriggerDestinations,
  updateTrigger,
  type Trigger,
  type TriggerCreateInput,
  type TriggerDestination,
} from "@/lib/triggers";

interface Props {
  open: boolean;
  initial?: Partial<Trigger>;
  onClose: () => void;
  onSaved: (t: Trigger) => void;
  /** Lock the scope_path input so callers (e.g. doc page) can pin it. */
  lockScope?: boolean;
}

// Fallback used while the catalog is loading or if the fetch fails — keeps
// the form usable on a transient network blip. Live values come from
// GET /api/triggers/destinations.
const FALLBACK_DESTINATIONS: TriggerDestination[] = [
  { id: "event_log", name: "Event Log", description: "Tracked in the event log only." },
];

const EXAMPLE_SCOPE = "projects/release-v3.md";
const EXAMPLE_IF = "the document is updated with a release version";
const EXAMPLE_SEND =
  "a message saying that the version has been finalized or updated to the specific version number.";

export function TriggerModal({ open, initial, onClose, onSaved, lockScope }: Props) {
  const isEdit = Boolean(initial?.id);
  const [scopePath, setScopePath] = useState("");
  const [ifText, setIfText] = useState("");
  const [sendText, setSendText] = useState("");
  const [destinations, setDestinations] = useState<TriggerDestination[]>(
    FALLBACK_DESTINATIONS,
  );
  const [destination, setDestination] = useState(FALLBACK_DESTINATIONS[0].id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setScopePath(initial?.scope_path ?? "");
    setIfText(initial?.nl_description ?? "");
    setSendText(initial?.message ?? "");
    setDestination(initial?.destination ?? FALLBACK_DESTINATIONS[0].id);
    setError(null);
  }, [
    open,
    initial?.id,
    initial?.scope_path,
    initial?.nl_description,
    initial?.message,
    initial?.destination,
  ]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    getTriggerDestinations()
      .then((rows) => {
        if (cancelled || rows.length === 0) return;
        setDestinations(rows);
        // If the current selection isn't in the catalog, fall back to the first row.
        setDestination((cur) => (rows.some((r) => r.id === cur) ? cur : rows[0].id));
      })
      .catch(() => {
        // Keep the fallback list silently — the form stays usable.
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) return null;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const nl = ifText.trim();
      const msg = sendText.trim();
      let saved: Trigger;
      if (isEdit && initial?.id) {
        saved = await updateTrigger(initial.id, {
          scope_path: scopePath.trim(),
          nl_description: nl,
          message: msg,
          destination,
        });
      } else {
        const input: TriggerCreateInput = {
          scope_path: scopePath.trim(),
          nl_description: nl,
          message: msg,
          destination,
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

  const canSave = scopePath.trim() && ifText.trim() && sendText.trim();
  const selectedDest = destinations.find((d) => d.id === destination);
  const destLabel = selectedDest?.name ?? destination;
  const destDescription = selectedDest?.description ?? "";

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
      {/* Blurred filled-in example, peeking out from behind the form. */}
      <div
        aria-hidden
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%) translate(40px, 44px) rotate(-1.5deg)",
          width: "min(560px, 92vw)",
          filter: "blur(3.5px)",
          opacity: 0.85,
          pointerEvents: "none",
          zIndex: 0,
        }}
      >
        <PreviewCard
          scope={EXAMPLE_SCOPE}
          ifText={EXAMPLE_IF}
          sendText={EXAMPLE_SEND}
          destLabel="Event log only"
        />
      </div>

      <form
        onSubmit={onSubmit}
        style={{
          position: "relative",
          background: "white",
          borderRadius: 14,
          width: "min(560px, 92vw)",
          maxHeight: "92vh",
          overflowY: "auto",
          padding: 24,
          boxShadow: "0 32px 80px rgba(0,0,0,0.28)",
          display: "flex",
          flexDirection: "column",
          gap: 16,
          zIndex: 1,
        }}
      >
        <div>
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>
            {isEdit ? "Edit trigger" : "Create a trigger"}
          </h2>
          <p style={{ margin: "8px 0 0", fontSize: 13, color: "#4b5563", lineHeight: 1.55 }}>
            A trigger keeps an eye on a doc (or folder) and reacts when something
            you care about changes. Tell us what to look for, what to say when it
            happens, and where to send the message. We'll watch the edits for you.
          </p>
        </div>

        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>Watching</span>
          <input
            value={scopePath}
            onChange={(e) => setScopePath(e.target.value)}
            disabled={busy || lockScope}
            placeholder="projects/foo.md or projects"
            style={inputStyle}
          />
          <span style={fieldHintStyle}>
            e.g. <code>projects/foo.md</code> for one doc, <code>projects</code>{" "}
            for a folder, or <code>/</code> to watch the whole wiki.
          </span>
        </label>

        <SentenceRow label="If" tone="if">
          <textarea
            value={ifText}
            onChange={(e) => setIfText(e.target.value)}
            disabled={busy}
            placeholder={EXAMPLE_IF}
            rows={2}
            style={{ ...inputStyle, fontFamily: "inherit", resize: "vertical" }}
          />
        </SentenceRow>

        <SentenceRow label="then send" tone="send">
          <textarea
            value={sendText}
            onChange={(e) => setSendText(e.target.value)}
            disabled={busy}
            placeholder={EXAMPLE_SEND}
            rows={2}
            style={{ ...inputStyle, fontFamily: "inherit", resize: "vertical" }}
          />
        </SentenceRow>

        <SentenceRow label="to" tone="to">
          <select
            value={destination}
            onChange={(e) => setDestination(e.target.value)}
            disabled={busy}
            style={{ ...inputStyle, cursor: "pointer", appearance: "auto" }}
          >
            {destinations.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </SentenceRow>

        {destDescription && (
          <p style={{ margin: 0, fontSize: 12, color: "#6b7280", lineHeight: 1.5 }}>
            {destDescription}
          </p>
        )}

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
            disabled={busy || !canSave}
            style={{ ...primaryBtn, opacity: busy || !canSave ? 0.6 : 1 }}
          >
            {busy ? "Saving…" : isEdit ? "Save" : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}

function SentenceRow({
  label,
  tone,
  children,
}: {
  label: string;
  tone: "if" | "send" | "to";
  children: ReactNode;
}) {
  const colors = {
    if: { bg: "#fffbeb", fg: "#92400e", border: "#fde68a" },
    send: { bg: "#ecfdf5", fg: "#047857", border: "#a7f3d0" },
    to: { bg: "#eef2ff", fg: "#4338ca", border: "#c7d2fe" },
  }[tone];
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
      <span
        style={{
          flexShrink: 0,
          marginTop: 6,
          padding: "3px 10px",
          background: colors.bg,
          color: colors.fg,
          border: `1px solid ${colors.border}`,
          borderRadius: 999,
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: "0.04em",
          textTransform: "uppercase",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>{children}</div>
    </div>
  );
}

function PreviewCard({
  scope,
  ifText,
  sendText,
  destLabel,
}: {
  scope: string;
  ifText: string;
  sendText: string;
  destLabel: string;
}) {
  return (
    <div
      style={{
        background: "white",
        borderRadius: 14,
        padding: 24,
        boxShadow: "0 12px 40px rgba(0,0,0,0.18)",
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      <div>
        <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>Create a trigger</h2>
        <p style={{ margin: "8px 0 0", fontSize: 13, color: "#4b5563" }}>
          A trigger keeps an eye on a doc and reacts when something changes.
        </p>
      </div>
      <div style={fieldStyle}>
        <span style={fieldLabelStyle}>Watching</span>
        <div style={{ ...inputStyle, color: "#111" }}>{scope}</div>
      </div>
      <SentenceRow label="If" tone="if">
        <div style={{ ...inputStyle, color: "#111", whiteSpace: "pre-wrap" }}>{ifText}</div>
      </SentenceRow>
      <SentenceRow label="then send" tone="send">
        <div style={{ ...inputStyle, color: "#111", whiteSpace: "pre-wrap" }}>{sendText}</div>
      </SentenceRow>
      <SentenceRow label="to" tone="to">
        <div style={{ ...inputStyle, color: "#111" }}>{destLabel}</div>
      </SentenceRow>
    </div>
  );
}

const fieldStyle: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 6 };

const fieldLabelStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  color: "#6b7280",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
};

const fieldHintStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#6b7280",
  lineHeight: 1.4,
};

const inputStyle: React.CSSProperties = {
  padding: "9px 11px",
  border: "1px solid #d1d5db",
  borderRadius: 6,
  fontSize: 14,
  outline: "none",
  width: "100%",
  boxSizing: "border-box",
  background: "white",
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

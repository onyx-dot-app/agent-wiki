"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/common/Button";
import {
  createTrigger,
  getTriggerDestinations,
  updateTrigger,
  type Trigger,
  type TriggerCreateInput,
  type TriggerDestination,
} from "@/lib/triggers";
import { color, radius, shadow } from "@/lib/theme";

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
        background: color.overlay,
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
          background: color.bg.page,
          borderRadius: radius.lg,
          width: "min(560px, 92vw)",
          maxHeight: "92vh",
          overflowY: "auto",
          padding: 24,
          boxShadow: shadow.modal,
          display: "flex",
          flexDirection: "column",
          gap: 16,
          zIndex: 1,
        }}
      >
        <div>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: color.text.primary }}>
            {isEdit ? "Edit trigger" : "Create a trigger"}
          </h2>
          <p style={{ margin: "6px 0 0", fontSize: 13, color: color.text.secondary, lineHeight: 1.55 }}>
            Triggers monitor documents or folders and send events when a
            specified change occurs. They run on document updates or on a
            schedule. Set the conditions to watch for, the event message to
            send, and where it should land.
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

        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>If</span>
          <textarea
            value={ifText}
            onChange={(e) => setIfText(e.target.value)}
            disabled={busy}
            placeholder={EXAMPLE_IF}
            rows={2}
            style={{ ...inputStyle, fontFamily: "inherit", resize: "vertical" }}
          />
        </label>

        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>Then send</span>
          <textarea
            value={sendText}
            onChange={(e) => setSendText(e.target.value)}
            disabled={busy}
            placeholder={EXAMPLE_SEND}
            rows={2}
            style={{ ...inputStyle, fontFamily: "inherit", resize: "vertical" }}
          />
        </label>

        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>To</span>
          <select
            value={destination}
            onChange={(e) => setDestination(e.target.value)}
            disabled={busy}
            style={{ ...inputStyle, cursor: "pointer" }}
          >
            {destinations.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          {destDescription && <span style={fieldHintStyle}>{destDescription}</span>}
        </label>

        {error && (
          <div
            style={{
              background: color.state.danger.bg,
              color: color.state.danger.fg,
              borderRadius: radius.sm,
              padding: 10,
              fontSize: 13,
            }}
          >
            {error}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <Button type="button" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={busy || !canSave}>
            {busy ? "Saving…" : isEdit ? "Save" : "Create"}
          </Button>
        </div>
      </form>
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
        background: color.bg.page,
        borderRadius: radius.lg,
        padding: 24,
        boxShadow: shadow.modal,
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      <div>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: color.text.primary }}>Create a trigger</h2>
        <p style={{ margin: "6px 0 0", fontSize: 13, color: color.text.secondary }}>
          Triggers monitor documents or folders and send events when a specified change occurs.
        </p>
      </div>
      <div style={fieldStyle}>
        <span style={fieldLabelStyle}>Watching</span>
        <div style={{ ...inputStyle, color: color.text.primary }}>{scope}</div>
      </div>
      <div style={fieldStyle}>
        <span style={fieldLabelStyle}>If</span>
        <div style={{ ...inputStyle, color: color.text.primary, whiteSpace: "pre-wrap" }}>{ifText}</div>
      </div>
      <div style={fieldStyle}>
        <span style={fieldLabelStyle}>Then send</span>
        <div style={{ ...inputStyle, color: color.text.primary, whiteSpace: "pre-wrap" }}>{sendText}</div>
      </div>
      <div style={fieldStyle}>
        <span style={fieldLabelStyle}>To</span>
        <div style={{ ...inputStyle, color: color.text.primary }}>{destLabel}</div>
      </div>
    </div>
  );
}

const fieldStyle: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 6 };

const fieldLabelStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  color: color.text.muted,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
};

const fieldHintStyle: React.CSSProperties = {
  fontSize: 12,
  color: color.text.muted,
  lineHeight: 1.4,
};

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  border: `1px solid ${color.border.default}`,
  borderRadius: radius.sm,
  fontSize: 14,
  outline: "none",
  width: "100%",
  boxSizing: "border-box",
  background: color.bg.page,
};

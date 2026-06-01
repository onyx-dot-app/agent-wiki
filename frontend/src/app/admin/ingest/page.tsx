"use client";

import { useEffect, useState, type FormEvent } from "react";
import { mutate as globalMutate } from "swr";

import { Button } from "@/components/common/Button";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
import { color, radius } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";

interface IngestSettings {
  max_doc_chars: number;
  api_key: string | null;
}

type Provider = "anthropic" | "openai" | "gemini" | "ollama";

interface LLMSettings {
  provider: Provider;
  model: string;
  anthropic_api_key_set: boolean;
  openai_api_key_set: boolean;
  gemini_api_key_set: boolean;
  ollama_base_url: string;
  provider_models: Record<string, string[]>;
  ingest_selector_model: string;
}

const PROVIDER_LABEL: Record<Provider, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Gemini",
  ollama: "Ollama",
};

const ALL_PROVIDERS: Provider[] = ["anthropic", "openai", "gemini", "ollama"];


function isConfigured(p: Provider, s: LLMSettings): boolean {
  if (p === "anthropic") return s.anthropic_api_key_set;
  if (p === "openai") return s.openai_api_key_set;
  if (p === "gemini") return s.gemini_api_key_set;
  if (p === "ollama") return !!s.ollama_base_url;
  return false;
}

export default function AdminIngestPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main style={{ padding: isMobile ? "16px 12px" : "24px 32px", maxWidth: 720 }}>
        <BackLink />
        <PageHeader
          title="Onyx connection"
          description="Connect your Onyx instance to automatically push indexed documents into this wiki. Copy the base URL and API key below into your Onyx environment variables."
        />
        <IngestForm />
      </main>
    </RequireAdmin>
  );
}

function IngestForm() {
  const [settings, setSettings] = useState<IngestSettings | null>(null);
  const [llmSettings, setLlmSettings] = useState<LLMSettings | null>(null);
  const [maxDocChars, setMaxDocChars] = useState("");
  const [keyVisible, setKeyVisible] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [baseUrl, setBaseUrl] = useState("");

  useEffect(() => {
    setBaseUrl(window.location.origin);
  }, []);

  async function load() {
    try {
      const [ingest, llm] = await Promise.all([
        apiFetch<IngestSettings>("/admin/ingest"),
        apiFetch<LLMSettings>("/admin/llm"),
      ]);
      setSettings(ingest);
      setMaxDocChars(String(ingest.max_doc_chars));
      setLlmSettings(llm);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!settings) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const r = await apiFetch<IngestSettings>("/admin/ingest", {
        method: "PUT",
        body: JSON.stringify({ max_doc_chars: Number(maxDocChars) }),
      });
      setSettings(r);
      setSaved("Saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function regenerateKey() {
    if (
      settings?.api_key &&
      !confirm("Regenerate the API key? The old key will stop working immediately.")
    )
      return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const r = await apiFetch<{ api_key: string }>("/admin/ingest/regenerate-key", {
        method: "POST",
      });
      setSettings((prev) => (prev ? { ...prev, api_key: r.api_key } : prev));
      setKeyVisible(true);
      setSaved("New API key generated. Copy it now — it will be masked after you leave this page.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to regenerate");
    } finally {
      setSaving(false);
    }
  }

  async function copyToClipboard(text: string) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // fallback: select text
    }
  }

  if (!settings || !llmSettings) return <LoadingSpinner />;

  const dirty = maxDocChars !== String(settings.max_doc_chars);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Connection details */}
      <section>
        <h3 style={{ margin: "0 0 12px", fontSize: 14, fontWeight: 600 }}>Connection details</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* Base URL */}
          <div>
            <div style={lblStyle}>Base URL</div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontFamily: "ui-monospace, monospace", fontSize: 13, color: color.text.primary }}>
                {baseUrl}/api/documents/ingest
              </span>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => void copyToClipboard(`${baseUrl}/api/documents/ingest`)}
              >
                Copy
              </Button>
            </div>
          </div>

          {/* API Key */}
          <div>
            <div style={lblStyle}>API key</div>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                readOnly
                type={keyVisible ? "text" : "password"}
                value={settings.api_key ?? ""}
                placeholder={settings.api_key ? undefined : "No key yet — click Regenerate"}
                style={{ ...inputStyle, flex: 1, fontFamily: settings.api_key ? "ui-monospace, monospace" : undefined }}
              />
              {settings.api_key && keyVisible && (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => void copyToClipboard(settings.api_key ?? "")}
                >
                  Copy
                </Button>
              )}
              <Button
                type="button"
                variant={settings.api_key ? "secondary" : "primary"}
                size="sm"
                disabled={saving}
                onClick={() => void regenerateKey()}
              >
                Regenerate
              </Button>
            </div>
          </div>
        </div>
      </section>

      {/* Max doc size */}
      <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>Ingest settings</h3>
        <label>
          <div style={lblStyle}>Max document size (characters)</div>
          <input
            type="number"
            min={1000}
            max={5000000}
            value={maxDocChars}
            onChange={(e) => setMaxDocChars(e.target.value)}
            style={{ ...inputStyle, width: 160 }}
          />
        </label>
        {error && <div style={{ color: color.state.danger.fg }}>{error}</div>}
        {saved && <div style={{ color: color.state.success.fg }}>{saved}</div>}
        <div>
          <Button type="submit" variant="primary" disabled={saving || !dirty}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </form>

      <div style={{ borderTop: `1px solid ${color.border.subtle}` }} />

      <SelectorModelSection settings={llmSettings} onSaved={() => void load()} />
    </div>
  );
}

function SelectorModelSection({ settings, onSaved }: { settings: LLMSettings; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [selModel, setSelModel] = useState(settings.ingest_selector_model || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const availableProviders = ALL_PROVIDERS.filter((p) => isConfigured(p, settings));
  const options = availableProviders.flatMap((p) =>
    (settings.provider_models[p] ?? []).map((m) => ({ provider: p, model: m })),
  );
  const hasNoModels = availableProviders.length > 0 && options.length === 0;

  useEffect(() => {
    setSelModel(settings.ingest_selector_model || "");
  }, [settings]);

  async function onSave() {
    setSaving(true);
    setError(null);
    try {
      await apiFetch("/admin/llm", {
        method: "PUT",
        body: JSON.stringify({ ingest_selector_model: selModel }),
      });
      setEditing(false);
      onSaved();
      void globalMutate("/llm/status");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  const activeLabel = selModel
    ? selModel === settings.model
      ? `${selModel} (same as main model — pre-filter disabled)`
      : selModel
    : "None — all documents go to the main model";

  return (
    <section>
      <h3 style={{ margin: "0 0 4px", fontSize: 14, fontWeight: 600 }}>Selector model</h3>
      <div style={{ color: color.text.muted, fontSize: 13, marginBottom: 12 }}>
        A faster, cheaper model that screens incoming documents before the main model decides what to update.
        Helps reduce cost when many documents are being pushed. Leave unset to send all documents straight to the main model.
      </div>
      {!editing ? (
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "12px 16px", border: `1px solid ${color.border.default}`,
          borderRadius: radius.md, background: color.bg.panel,
        }}>
          <span style={{ fontSize: 14, color: selModel && selModel !== settings.model ? color.text.primary : color.text.muted }}>
            {activeLabel}
          </span>
          <Button size="sm" variant="secondary" onClick={() => setEditing(true)} disabled={availableProviders.length === 0}>Edit</Button>
        </div>
      ) : (
        <div style={{
          border: `1px solid ${color.border.default}`,
          borderRadius: radius.md, background: color.bg.panel,
          display: "flex", flexDirection: "column",
        }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, padding: 12 }}>
            {hasNoModels && (
              <div style={{ fontSize: 13, color: color.text.muted, padding: "4px 0 8px" }}>
                No models configured. Add models on the{" "}
                <a href="/admin/language-models" style={{ color: color.accent.fg }}>Language models</a> page first.
              </div>
            )}
            <button
              type="button"
              onClick={() => setSelModel("")}
              style={{
                display: "flex", alignItems: "center", gap: 12, padding: "10px 12px",
                border: `1px solid ${selModel === "" ? color.accent.subtleBorder : color.border.default}`,
                borderRadius: radius.sm,
                background: selModel === "" ? color.accent.subtleBg : color.bg.page,
                cursor: "pointer", textAlign: "left",
              }}
            >
              <div style={{
                width: 16, height: 16, borderRadius: "50%", flexShrink: 0,
                border: selModel === "" ? "none" : `1.5px solid ${color.border.strong}`,
                background: selModel === "" ? color.accent.bg : "transparent",
                display: "flex", alignItems: "center", justifyContent: "center",
              }}>
                {selModel === "" && <div style={{ width: 8, height: 8, borderRadius: "50%", background: color.accent.fg }} />}
              </div>
              <span style={{ fontSize: 13, color: selModel === "" ? color.accent.subtleFg : color.text.muted, fontStyle: "italic" }}>
                None — all documents go to the main model
              </span>
            </button>
            {options.map(({ provider: p, model: m }) => {
              const isSelected = selModel === m;
              return (
                <button
                  key={`${p}:${m}`}
                  type="button"
                  onClick={() => setSelModel(m)}
                  style={{
                    display: "flex", alignItems: "center", gap: 12, padding: "10px 12px",
                    border: `1px solid ${isSelected ? color.accent.subtleBorder : color.border.default}`,
                    borderRadius: radius.sm,
                    background: isSelected ? color.accent.subtleBg : color.bg.page,
                    cursor: "pointer", textAlign: "left",
                  }}
                >
                  <div style={{
                    width: 16, height: 16, borderRadius: "50%", flexShrink: 0,
                    border: isSelected ? "none" : `1.5px solid ${color.border.strong}`,
                    background: isSelected ? color.accent.bg : "transparent",
                    display: "flex", alignItems: "center", justifyContent: "center",
                  }}>
                    {isSelected && <div style={{ width: 8, height: 8, borderRadius: "50%", background: color.accent.fg }} />}
                  </div>
                  <span style={{ fontSize: 13, fontWeight: 500, color: isSelected ? color.accent.subtleFg : color.text.secondary, flexShrink: 0 }}>
                    {PROVIDER_LABEL[p]}
                  </span>
                  <span style={{ fontSize: 13, color: isSelected ? color.accent.subtleFg : color.text.muted, fontFamily: "ui-monospace, monospace" }}>
                    {m}
                  </span>
                  {m === settings.model && (
                    <span style={{ fontSize: 11, color: color.text.muted, marginLeft: "auto" }}>same as main — pre-filter disabled</span>
                  )}
                </button>
              );
            })}
          </div>
          {error && <div style={{ color: color.state.danger.fg, fontSize: 13, padding: "0 12px 8px" }}>{error}</div>}
          <div style={{ display: "flex", gap: 8, padding: "4px 12px 12px" }}>
            <Button type="button" variant="primary" size="sm" disabled={saving} onClick={() => void onSave()}>
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={() => { setEditing(false); setError(null); setSelModel(settings.ingest_selector_model || ""); }}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  boxSizing: "border-box",
  border: `1px solid ${color.border.default}`,
  borderRadius: radius.sm,
  fontSize: 14,
};
const lblStyle: React.CSSProperties = { marginBottom: 4, fontSize: 13, fontWeight: 500 };
const hintStyle: React.CSSProperties = {
  fontWeight: 400,
  color: color.text.muted,
  fontFamily: "ui-monospace, monospace",
  fontSize: 12,
};

"use client";

import { useEffect, useState, type FormEvent } from "react";
import { mutate as globalMutate } from "swr";

import { Button } from "@onyx-ai/opal/components";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
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
      <main className={`max-w-[720px] ${isMobile ? "px-3 py-4" : "px-8 py-6"}`}>
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
    <div className="flex flex-col gap-6">
      {/* Connection details */}
      <section>
        <h3 className="m-0 mb-3 text-sm font-semibold">Connection details</h3>
        <div className="flex flex-col gap-3">
          {/* Base URL */}
          <div>
            <div className="mb-1 text-[13px] font-medium">Base URL</div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-[13px] text-(--text-05)">
                {baseUrl}/api/documents/ingest
              </span>
              <Button
                type="button"
                variant="default"
                size="sm"
                onClick={() => void copyToClipboard(`${baseUrl}/api/documents/ingest`)}
              >
                Copy
              </Button>
            </div>
          </div>

          {/* API Key */}
          <div>
            <div className="mb-1 text-[13px] font-medium">API key</div>
            <div className="flex gap-2">
              <input
                readOnly
                type={keyVisible ? "text" : "password"}
                value={settings.api_key ?? ""}
                placeholder={settings.api_key ? undefined : "No key yet — click Regenerate"}
                className={`flex-1 w-full box-border py-2 px-[10px] border border-(--border-01) rounded-(--border-radius-04) text-sm${settings.api_key ? " font-mono" : ""}`}
              />
              {settings.api_key && keyVisible && (
                <Button
                  type="button"
                  variant="default"
                  size="sm"
                  onClick={() => void copyToClipboard(settings.api_key ?? "")}
                >
                  Copy
                </Button>
              )}
              <Button
                type="button"
                variant={settings.api_key ? "default" : "action"}
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
      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <h3 className="m-0 text-sm font-semibold">Ingest settings</h3>
        <label>
          <div className="mb-1 text-[13px] font-medium">Max document size (characters)</div>
          <input
            type="number"
            min={1000}
            max={5000000}
            value={maxDocChars}
            onChange={(e) => setMaxDocChars(e.target.value)}
            className="w-[160px] box-border py-2 px-[10px] border border-(--border-01) rounded-(--border-radius-04) text-sm"
          />
        </label>
        {error && <div className="text-(--status-text-error-05)">{error}</div>}
        {saved && <div className="text-(--status-text-success-05)">{saved}</div>}
        <div>
          <Button type="submit" variant="action" disabled={saving || !dirty}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </form>

      <div className="border-t border-(--border-01)" />

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
      <h3 className="m-0 mb-1 text-sm font-semibold">Selector model</h3>
      <div className="text-(--text-03) text-[13px] mb-3">
        A faster, cheaper model that screens incoming documents before the main model decides what to update.
        Helps reduce cost when many documents are being pushed. Leave unset to send all documents straight to the main model.
      </div>
      {!editing ? (
        <div className="flex items-center justify-between py-3 px-4 border border-(--border-01) rounded-(--border-radius-08) bg-(--background-tint-01)">
          <span className={`text-sm ${selModel && selModel !== settings.model ? "text-(--text-05)" : "text-(--text-03)"}`}>
            {activeLabel}
          </span>
          <Button size="sm" variant="default" onClick={() => setEditing(true)} disabled={availableProviders.length === 0}>Edit</Button>
        </div>
      ) : (
        <div className="border border-(--border-01) rounded-(--border-radius-08) bg-(--background-tint-01) flex flex-col">
          <div className="flex flex-col gap-1 p-3">
            {hasNoModels && (
              <div className="text-[13px] text-(--text-03) py-1 pb-2">
                No models configured. Add models on the{" "}
                <a href="/admin/language-models" className="text-(--text-inverted-05)">Language models</a> page first.
              </div>
            )}
            <button
              type="button"
              onClick={() => setSelModel("")}
              className={`flex items-center gap-3 py-[10px] px-3 border rounded-(--border-radius-04) cursor-pointer text-left ${selModel === "" ? "border-(--border-01) bg-(--background-tint-03)" : "border-(--border-01) bg-(--background-tint-00)"}`}
            >
              <div className={`w-4 h-4 rounded-full shrink-0 flex items-center justify-center ${selModel === "" ? "bg-(--background-tint-inverted-00)" : "border-[1.5px] border-(--border-02) bg-transparent"}`}>
                {selModel === "" && <div className="w-2 h-2 rounded-full bg-(--text-inverted-05)" />}
              </div>
              <span className={`text-[13px] italic ${selModel === "" ? "text-(--text-05)" : "text-(--text-03)"}`}>
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
                  className={`flex items-center gap-3 py-[10px] px-3 border rounded-(--border-radius-04) cursor-pointer text-left ${isSelected ? "border-(--border-01) bg-(--background-tint-03)" : "border-(--border-01) bg-(--background-tint-00)"}`}
                >
                  <div className={`w-4 h-4 rounded-full shrink-0 flex items-center justify-center ${isSelected ? "bg-(--background-tint-inverted-00)" : "border-[1.5px] border-(--border-02) bg-transparent"}`}>
                    {isSelected && <div className="w-2 h-2 rounded-full bg-(--text-inverted-05)" />}
                  </div>
                  <span className={`text-[13px] font-medium shrink-0 ${isSelected ? "text-(--text-05)" : "text-(--text-04)"}`}>
                    {PROVIDER_LABEL[p]}
                  </span>
                  <span className={`text-[13px] font-mono ${isSelected ? "text-(--text-05)" : "text-(--text-03)"}`}>
                    {m}
                  </span>
                  {m === settings.model && (
                    <span className="text-[11px] text-(--text-03) ml-auto">same as main — pre-filter disabled</span>
                  )}
                </button>
              );
            })}
          </div>
          {error && <div className="text-(--status-text-error-05) text-[13px] px-3 pb-2">{error}</div>}
          <div className="flex gap-2 px-3 pt-1 pb-3">
            <Button type="button" variant="action" size="sm" disabled={saving} onClick={() => void onSave()}>
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button type="button" variant="default" size="sm" onClick={() => { setEditing(false); setError(null); setSelModel(settings.ingest_selector_model || ""); }}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

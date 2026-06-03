"use client";

import { useEffect, useState, type FormEvent } from "react";
import { mutate as globalMutate } from "swr";

import { Button } from "@onyx-ai/opal/components";
import { SvgCheckSmall } from "@onyx-ai/opal/icons";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
import { useIsMobile } from "@/lib/viewport";

type Provider = "anthropic" | "openai" | "gemini" | "ollama";

interface ProviderMeta {
  label: string;
  defaultModel: string;
  keyLabel: string;
  keyPlaceholder: string;
  initial: string;
}

const PROVIDER_META: Record<Provider, ProviderMeta> = {
  anthropic: { label: "Anthropic", defaultModel: "claude-sonnet-4-6", keyLabel: "API key", keyPlaceholder: "sk-ant-…", initial: "A" },
  openai:    { label: "OpenAI",    defaultModel: "gpt-5.5",          keyLabel: "API key", keyPlaceholder: "sk-…",     initial: "O" },
  gemini:    { label: "Gemini",    defaultModel: "gemini-3.1-pro-preview", keyLabel: "API key", keyPlaceholder: "AIza…",    initial: "G" },
  ollama:    { label: "Ollama",    defaultModel: "llama3.1",         keyLabel: "Base URL", keyPlaceholder: "http://localhost:11434", initial: "L" },
};

const ALL_PROVIDERS: Provider[] = ["anthropic", "openai", "gemini", "ollama"];

const PROVIDER_MODELS: Record<Provider, string[]> = {
  anthropic: [
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-haiku-4-5",
  ],
  openai: [
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.2",
  ],
  gemini: [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
  ],
  ollama: [
    "llama3.1",
    "llama3.2",
    "mistral",
    "phi3",
    "qwen2.5",
    "deepseek-r1",
  ],
};


interface LLMSettings {
  provider: Provider;
  model: string;
  anthropic_api_key_set: boolean;
  openai_api_key_set: boolean;
  gemini_api_key_set: boolean;
  anthropic_api_key_hint: string;
  openai_api_key_hint: string;
  gemini_api_key_hint: string;
  ollama_base_url: string;
  provider_models: Record<string, string[]>;
}

function isConfigured(p: Provider, s: LLMSettings): boolean {
  if (p === "anthropic") return s.anthropic_api_key_set;
  if (p === "openai") return s.openai_api_key_set;
  if (p === "gemini") return s.gemini_api_key_set;
  if (p === "ollama") return !!s.ollama_base_url;
  return false;
}

function keyHint(p: Provider, s: LLMSettings): string {
  if (p === "anthropic") return s.anthropic_api_key_hint;
  if (p === "openai") return s.openai_api_key_hint;
  if (p === "gemini") return s.gemini_api_key_hint;
  if (p === "ollama") return s.ollama_base_url || "http://localhost:11434";
  return "";
}

export default function AdminLLMPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main className={`max-w-[760px] ${isMobile ? "py-4 px-3" : "py-6 px-8"}`}>
        <BackLink />
        <PageHeader
          title="Language models"
          description="Manage provider credentials and set the model used by agents. Users can override the model for their own chats in Settings."
        />
        <LLMPage />
      </main>
    </RequireAdmin>
  );
}

function LLMPage() {
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedProvider, setExpandedProvider] = useState<Provider | null>(null);

  async function load() {
    try {
      const r = await apiFetch<LLMSettings>("/admin/llm");
      setSettings(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }

  useEffect(() => { void load(); }, []);

  if (error) return <div className="text-(--status-text-error-05)">{error}</div>;
  if (!settings) return <LoadingSpinner />;

  const configured = ALL_PROVIDERS.filter((p) => isConfigured(p, settings));
  const unconfigured = ALL_PROVIDERS.filter((p) => !isConfigured(p, settings));

  function toggle(p: Provider) {
    setExpandedProvider((prev) => (prev === p ? null : p));
  }

  return (
    <div className="flex flex-col gap-8">
      <AgentModelSection settings={settings} onSaved={load} />

      <div className="border-t border-(--border-01)" />

      {/* Available Providers */}
      <section>
        <div className={sectionHeaderClass}>Available providers</div>
        {configured.length === 0 && (
          <div className="text-(--text-03) text-sm">No providers configured yet.</div>
        )}
        <div className="flex flex-col gap-2">
          {configured.map((p) => (
            <ProviderCard
              key={p}
              provider={p}
              settings={settings}
              isActive={settings.provider === p}
              expanded={expandedProvider === p}
              onToggle={() => toggle(p)}
              onSaved={() => { void load(); setExpandedProvider(null); }}
            />
          ))}
        </div>
      </section>

      {/* Add Provider */}
      {unconfigured.length > 0 && (
        <>
          <div className="border-t border-(--border-01)" />
          <section>
            <div className={sectionHeaderClass}>Add provider</div>
            <div className="text-(--text-03) text-[13px] mb-3">
              Connect a provider to make it available for agent and chat use.
            </div>
            <div className="flex flex-col gap-2">
              {unconfigured.map((p) => (
                <ProviderCard
                  key={p}
                  provider={p}
                  settings={settings}
                  isActive={false}
                  expanded={expandedProvider === p}
                  onToggle={() => toggle(p)}
                  onSaved={() => { void load(); setExpandedProvider(null); }}
                />
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function AgentModelSection({ settings, onSaved }: { settings: LLMSettings; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [selProvider, setSelProvider] = useState<Provider | "">(settings.provider as Provider | "");
  const [selModel, setSelModel] = useState(settings.model);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const availableProviders = ALL_PROVIDERS.filter((p) => isConfigured(p, settings));

  // Flat list of every configured-provider + enabled-model combination.
  const options = availableProviders.flatMap((p) => {
    const models = settings.provider_models[p]?.length
      ? settings.provider_models[p]
      : PROVIDER_MODELS[p] ?? [];
    return models.map((m) => ({ provider: p, model: m }));
  });

  useEffect(() => {
    setSelProvider(settings.provider as Provider | "");
    setSelModel(settings.model);
  }, [settings]);

  async function onSave() {
    if (!selProvider) return;
    setSaving(true);
    setError(null);
    try {
      await apiFetch("/admin/llm", {
        method: "PUT",
        body: JSON.stringify({ provider: selProvider, model: selModel }),
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

  return (
    <section>
      <div className={sectionHeaderClass}>Default model</div>
      {!editing ? (
        <div className="flex items-center justify-between py-3 px-4 border border-(--border-01) rounded-(--border-radius-08) bg-(--background-tint-01)">
          <div>
            {settings.provider && PROVIDER_META[settings.provider as Provider] ? (
              <>
                <span className="text-sm font-medium">{PROVIDER_META[settings.provider as Provider].label}</span>
                <span className="text-sm text-(--text-03) ml-2">{settings.model || "—"}</span>
              </>
            ) : (
              <span className="text-sm text-(--text-03)">No model selected — configure a provider below.</span>
            )}
          </div>
          <Button size="sm" variant="default" onClick={() => setEditing(true)} disabled={availableProviders.length === 0}>Edit</Button>
        </div>
      ) : (
        <div className="border border-(--border-01) rounded-(--border-radius-08) bg-(--background-tint-01) flex flex-col">
          <div className="flex flex-col gap-1 p-3">
            {options.map(({ provider: p, model: m }) => {
              const isSelected = selProvider === p && selModel === m;
              return (
                <button
                  key={`${p}:${m}`}
                  type="button"
                  onClick={() => { setSelProvider(p); setSelModel(m); }}
                  className={`flex items-center gap-3 py-[10px] px-3 border rounded-(--border-radius-04) cursor-pointer text-left ${isSelected ? "border-(--border-01) bg-(--background-tint-03)" : "border-(--border-01) bg-(--background-tint-00)"}`}
                >
                  <div
                    className={`w-[16px] h-[16px] rounded-full shrink-0 flex items-center justify-center ${isSelected ? "bg-(--background-tint-inverted-00) border-none" : "bg-transparent border-[1.5px] border-(--border-02)"}`}
                  >
                    {isSelected && <div className="w-[8px] h-[8px] rounded-full bg-(--text-inverted-05)" />}
                  </div>
                  <span className={`text-[13px] font-medium shrink-0 ${isSelected ? "text-(--text-05)" : "text-(--text-04)"}`}>
                    {PROVIDER_META[p].label}
                  </span>
                  <span className={`text-[13px] font-mono ${isSelected ? "text-(--text-05)" : "text-(--text-03)"}`}>
                    {m}
                  </span>
                </button>
              );
            })}
          </div>
          {error && <div className="text-(--status-text-error-05) text-[13px] px-3 pb-2">{error}</div>}
          <div className="flex gap-2 pt-1 px-3 pb-3">
            <Button type="button" variant="action" size="sm" disabled={saving || !selProvider} onClick={() => void onSave()}>
              {saving ? "Saving…" : "Set as active"}
            </Button>
            <Button type="button" variant="default" size="sm" onClick={() => { setEditing(false); setError(null); }}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

function ProviderCard({
  provider, settings, isActive, expanded, onToggle, onSaved,
}: {
  provider: Provider;
  settings: LLMSettings;
  isActive: boolean;
  expanded: boolean;
  onToggle: () => void;
  onSaved: () => void;
}) {
  const meta = PROVIDER_META[provider];
  const configured = isConfigured(provider, settings);
  const hint = keyHint(provider, settings);

  return (
    <div className="border border-(--border-01) rounded-(--border-radius-08) overflow-hidden">
      {/* Card header row */}
      <div className="flex items-center gap-3 py-3 px-4 bg-(--background-tint-01)">
        {/* Provider initial icon */}
        <div className="w-[32px] h-[32px] rounded-(--border-radius-04) bg-(--background-tint-02) flex items-center justify-center text-[13px] font-bold text-(--text-04) shrink-0">
          {meta.initial}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{meta.label}</span>
            {isActive && (
              <span className="text-[11px] font-semibold py-[2px] px-[6px] rounded-full bg-(--background-tint-03) text-(--text-05) border border-(--border-01)">Agent</span>
            )}
          </div>
          {configured && hint && (
            <div className="text-xs text-(--text-03) font-mono mt-[2px]">
              {hint}
            </div>
          )}
        </div>

        <Button
          type="button"
          size="sm"
          variant={configured ? "default" : "action"}
          onClick={onToggle}
        >
          {configured ? (expanded ? "Close" : "Edit") : (expanded ? "Close" : "Connect")}
        </Button>
      </div>

      {/* Expanded form */}
      {expanded && (
        <div className="border-t border-(--border-01) p-4 bg-(--background-tint-00)">
          <ProviderForm
            provider={provider}
            settings={settings}
            configured={configured}
            onSaved={onSaved}
          />
        </div>
      )}
    </div>
  );
}

function ProviderForm({
  provider, settings, configured, onSaved,
}: {
  provider: Provider;
  settings: LLMSettings;
  configured: boolean;
  onSaved: () => void;
}) {
  const meta = PROVIDER_META[provider];
  const isOllama = provider === "ollama";
  const knownModels = PROVIDER_MODELS[provider];
  const savedModels = settings.provider_models[provider] ?? [];
  const [keyValue, setKeyValue] = useState("");
  const [selectedModels, setSelectedModels] = useState<Set<string>>(
    () => new Set(savedModels.length ? savedModels : knownModels),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const keyField = `${provider}_api_key` as "anthropic_api_key" | "openai_api_key" | "gemini_api_key";
  const currentHint = keyHint(provider, settings);

  function toggleModel(id: string) {
    setSelectedModels((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const body: Record<string, unknown> = {};
      if (isOllama) {
        body.ollama_base_url = keyValue === "" ? null : keyValue;
      } else if (keyValue) {
        body[keyField] = keyValue;
      }
      const enabledModels = knownModels.filter((m) => selectedModels.has(m));
      const updated = { ...settings.provider_models, [provider]: enabledModels };
      body.provider_models = updated;
      await apiFetch("/admin/llm", { method: "PUT", body: JSON.stringify(body) });
      setKeyValue("");
      setSaved(true);
      onSaved();
      void globalMutate("/llm/status");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function onClear() {
    if (!confirm(`Remove ${meta.label} credentials?`)) return;
    setSaving(true);
    setError(null);
    try {
      const body: Record<string, unknown> = isOllama ? { ollama_base_url: null } : { [keyField]: null };
      await apiFetch("/admin/llm", { method: "PUT", body: JSON.stringify(body) });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to remove");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <label>
        <div className={lblClass}>{meta.keyLabel}</div>
        <input
          type={isOllama ? "text" : "password"}
          value={keyValue}
          onChange={(e) => setKeyValue(e.target.value)}
          placeholder={configured ? (isOllama ? currentHint : "leave blank to keep current") : meta.keyPlaceholder}
          className={inputClass}
        />
      </label>

      <div>
        <div className="flex items-baseline justify-between mb-2">
          <div className={lblClass}>Models</div>
          <div className="text-xs text-(--text-03)">Select models to make available</div>
        </div>
        <div className="flex flex-col gap-1.5">
          {knownModels.map((id) => {
            const checked = selectedModels.has(id);
            return (
              <button
                key={id}
                type="button"
                onClick={() => toggleModel(id)}
                className={`flex items-center gap-[10px] py-[10px] px-3 border rounded-(--border-radius-08) cursor-pointer text-left ${checked ? "border-(--border-01) bg-(--background-tint-03)" : "border-(--border-01) bg-(--background-tint-00)"}`}
              >
                <div
                  className={`w-[18px] h-[18px] rounded-(--border-radius-04) shrink-0 flex items-center justify-center ${checked ? "bg-(--background-tint-inverted-00) border-none" : "bg-transparent border-[1.5px] border-(--border-02)"}`}
                >
                  {checked && (
                    <span className="text-(--text-inverted-05) flex">
                      <SvgCheckSmall size={11} />
                    </span>
                  )}
                </div>
                <span className={`text-[13px] font-mono ${checked ? "text-(--text-05)" : "text-(--text-05)"}`}>
                  {id}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {error && <div className="text-(--status-text-error-05) text-[13px]">{error}</div>}
      {saved && <div className="text-(--status-text-success-05) text-[13px]">Saved.</div>}
      <div className="flex gap-2">
        <Button type="submit" variant="action" size="sm" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
        {configured && (
          <Button type="button" variant="danger" size="sm" disabled={saving} onClick={onClear}>
            Remove
          </Button>
        )}
      </div>
    </form>
  );
}

const inputClass = "w-full py-2 px-[10px] box-border border border-(--border-01) rounded-(--border-radius-04) text-sm";
const lblClass = "mb-1 text-[13px] font-medium";
const sectionHeaderClass = "text-[13px] font-semibold text-(--text-04) uppercase tracking-[0.05em] mb-3";

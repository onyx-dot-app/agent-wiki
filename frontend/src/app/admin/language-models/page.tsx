"use client";

import { useEffect, useState, type FormEvent } from "react";
import { mutate as globalMutate } from "swr";

import { Button } from "@/components/common/Button";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
import { color, radius, shadow } from "@/lib/theme";
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
      <main style={{ padding: isMobile ? "16px 12px" : "24px 32px", maxWidth: 760 }}>
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

  if (error) return <div style={{ color: color.state.danger.fg }}>{error}</div>;
  if (!settings) return <LoadingSpinner />;

  const configured = ALL_PROVIDERS.filter((p) => isConfigured(p, settings));
  const unconfigured = ALL_PROVIDERS.filter((p) => !isConfigured(p, settings));

  function toggle(p: Provider) {
    setExpandedProvider((prev) => (prev === p ? null : p));
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
      <AgentModelSection settings={settings} onSaved={load} />

      <div style={{ borderTop: `1px solid ${color.border.subtle}` }} />

      {/* Available Providers */}
      <section>
        <div style={sectionHeaderStyle}>Available providers</div>
        {configured.length === 0 && (
          <div style={{ color: color.text.muted, fontSize: 14 }}>No providers configured yet.</div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
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
          <div style={{ borderTop: `1px solid ${color.border.subtle}` }} />
          <section>
            <div style={sectionHeaderStyle}>Add provider</div>
            <div style={{ color: color.text.muted, fontSize: 13, marginBottom: 12 }}>
              Connect a provider to make it available for agent and chat use.
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
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
      <div style={sectionHeaderStyle}>Default model</div>
      {!editing ? (
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "12px 16px", border: `1px solid ${color.border.default}`,
          borderRadius: radius.md, background: color.bg.panel,
        }}>
          <div>
            {settings.provider && PROVIDER_META[settings.provider as Provider] ? (
              <>
                <span style={{ fontSize: 14, fontWeight: 500 }}>{PROVIDER_META[settings.provider as Provider].label}</span>
                <span style={{ fontSize: 14, color: color.text.muted, marginLeft: 8 }}>{settings.model || "—"}</span>
              </>
            ) : (
              <span style={{ fontSize: 14, color: color.text.muted }}>No model selected — configure a provider below.</span>
            )}
          </div>
          <Button size="sm" variant="secondary" onClick={() => setEditing(true)} disabled={availableProviders.length === 0}>Edit</Button>
        </div>
      ) : (
        <div style={{
          border: `1px solid ${color.border.default}`,
          borderRadius: radius.md, background: color.bg.panel,
          display: "flex", flexDirection: "column",
        }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, padding: 12 }}>
            {options.map(({ provider: p, model: m }) => {
              const isSelected = selProvider === p && selModel === m;
              return (
                <button
                  key={`${p}:${m}`}
                  type="button"
                  onClick={() => { setSelProvider(p); setSelModel(m); }}
                  style={{
                    display: "flex", alignItems: "center", gap: 12,
                    padding: "10px 12px",
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
                    {PROVIDER_META[p].label}
                  </span>
                  <span style={{ fontSize: 13, color: isSelected ? color.accent.subtleFg : color.text.muted, fontFamily: "ui-monospace, monospace" }}>
                    {m}
                  </span>
                </button>
              );
            })}
          </div>
          {error && <div style={{ color: color.state.danger.fg, fontSize: 13, padding: "0 12px 8px" }}>{error}</div>}
          <div style={{ display: "flex", gap: 8, padding: "4px 12px 12px" }}>
            <Button type="button" variant="primary" size="sm" disabled={saving || !selProvider} onClick={() => void onSave()}>
              {saving ? "Saving…" : "Set as active"}
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={() => { setEditing(false); setError(null); }}>
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
    <div style={{
      border: `1px solid ${color.border.default}`,
      borderRadius: radius.md,
      overflow: "hidden",
    }}>
      {/* Card header row */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "12px 16px", background: color.bg.panel,
      }}>
        {/* Provider initial icon */}
        <div style={{
          width: 32, height: 32, borderRadius: radius.sm,
          background: color.bg.sunken, display: "flex", alignItems: "center",
          justifyContent: "center", fontSize: 13, fontWeight: 700,
          color: color.text.secondary, flexShrink: 0,
        }}>
          {meta.initial}
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 14, fontWeight: 500 }}>{meta.label}</span>
            {isActive && (
              <span style={{
                fontSize: 11, fontWeight: 600, padding: "2px 6px",
                borderRadius: radius.pill, background: color.accent.subtleBg,
                color: color.accent.subtleFg, border: `1px solid ${color.accent.subtleBorder}`,
              }}>Agent</span>
            )}
          </div>
          {configured && hint && (
            <div style={{ fontSize: 12, color: color.text.muted, fontFamily: "ui-monospace, monospace", marginTop: 2 }}>
              {hint}
            </div>
          )}
        </div>

        <Button
          type="button"
          size="sm"
          variant={configured ? "secondary" : "primary"}
          onClick={onToggle}
        >
          {configured ? (expanded ? "Close" : "Edit") : (expanded ? "Close" : "Connect")}
        </Button>
      </div>

      {/* Expanded form */}
      {expanded && (
        <div style={{ borderTop: `1px solid ${color.border.subtle}`, padding: 16, background: color.bg.page }}>
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
    <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <label>
        <div style={lblStyle}>{meta.keyLabel}</div>
        <input
          type={isOllama ? "text" : "password"}
          value={keyValue}
          onChange={(e) => setKeyValue(e.target.value)}
          placeholder={configured ? (isOllama ? currentHint : "leave blank to keep current") : meta.keyPlaceholder}
          style={inputStyle}
        />
      </label>

      <div>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 8 }}>
          <div style={lblStyle}>Models</div>
          <div style={{ fontSize: 12, color: color.text.muted }}>Select models to make available</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {knownModels.map((id) => {
            const checked = selectedModels.has(id);
            return (
              <button
                key={id}
                type="button"
                onClick={() => toggleModel(id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "10px 12px",
                  border: `1px solid ${checked ? color.accent.subtleBorder : color.border.default}`,
                  borderRadius: radius.md,
                  background: checked ? color.accent.subtleBg : color.bg.page,
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                <div style={{
                  width: 18, height: 18, borderRadius: radius.xs, flexShrink: 0,
                  border: checked ? "none" : `1.5px solid ${color.border.strong}`,
                  background: checked ? color.accent.bg : "transparent",
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}>
                  {checked && (
                    <svg width="11" height="11" viewBox="0 0 12 12" fill="none">
                      <path d="M2 6l3 3 5-5" stroke={color.accent.fg} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                </div>
                <span style={{ fontSize: 13, color: checked ? color.accent.subtleFg : color.text.primary, fontFamily: "ui-monospace, monospace" }}>
                  {id}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {error && <div style={{ color: color.state.danger.fg, fontSize: 13 }}>{error}</div>}
      {saved && <div style={{ color: color.state.success.fg, fontSize: 13 }}>Saved.</div>}
      <div style={{ display: "flex", gap: 8 }}>
        <Button type="submit" variant="primary" size="sm" disabled={saving}>
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

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  boxSizing: "border-box",
  border: `1px solid ${color.border.default}`,
  borderRadius: radius.sm,
  fontSize: 14,
};
const lblStyle: React.CSSProperties = { marginBottom: 4, fontSize: 13, fontWeight: 500 };
const sectionHeaderStyle: React.CSSProperties = {
  fontSize: 13, fontWeight: 600, color: color.text.secondary,
  textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12,
};

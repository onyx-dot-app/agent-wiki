"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/common/AppShell";
import { Button } from "@/components/common/Button";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { apiFetch } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { color, radius } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";

type Provider = "anthropic" | "openai" | "gemini" | "ollama";

const PROVIDERS: { value: Provider; label: string; modelHint: string }[] = [
  { value: "anthropic", label: "Anthropic", modelHint: "claude-opus-4-7" },
  { value: "openai", label: "OpenAI", modelHint: "gpt-4o" },
  { value: "gemini", label: "Gemini", modelHint: "gemini-2.5-pro" },
  { value: "ollama", label: "Ollama (local)", modelHint: "llama3.1" },
];

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
}

export default function AdminLLMPage() {
  const { user, loading } = useRequireAuth();
  const router = useRouter();
  const isMobile = useIsMobile();

  useEffect(() => {
    if (!loading && user && !user.is_admin) router.replace("/");
  }, [loading, user, router]);

  if (loading || !user) return <main style={{ padding: isMobile ? 16 : 32 }}>Loading…</main>;
  if (!user.is_admin) return null;

  return (
    <AppShell>
      <main style={{ padding: isMobile ? "16px 12px" : "24px 32px", maxWidth: 720 }}>
        <BackLink />
        <PageHeader
          title="LLM configuration"
          description="Provider, model, and credentials used for chat, the document updater, and trigger evaluations. Secrets are stored in the database and never echoed back to the browser."
        />
        <LLMForm />
      </main>
    </AppShell>
  );
}

function LLMForm() {
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [provider, setProvider] = useState<Provider>("anthropic");
  const [model, setModel] = useState("");
  const [anthropicKey, setAnthropicKey] = useState("");
  const [openaiKey, setOpenaiKey] = useState("");
  const [geminiKey, setGeminiKey] = useState("");
  const [ollamaBaseUrl, setOllamaBaseUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      const r = await apiFetch<LLMSettings>("/admin/llm");
      setSettings(r);
      setProvider(PROVIDERS.some((p) => p.value === r.provider) ? r.provider : "anthropic");
      setModel(r.model);
      setOllamaBaseUrl(r.ollama_base_url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const body: Record<string, unknown> = { provider, model };
      if (anthropicKey) body.anthropic_api_key = anthropicKey;
      if (openaiKey) body.openai_api_key = openaiKey;
      if (geminiKey) body.gemini_api_key = geminiKey;
      // The base URL isn't secret — send it whenever it's been edited so
      // empty-string ("use default") is reachable by clearing the field.
      if (settings && ollamaBaseUrl !== settings.ollama_base_url) {
        body.ollama_base_url = ollamaBaseUrl === "" ? null : ollamaBaseUrl;
      }
      await apiFetch<LLMSettings>("/admin/llm", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      setAnthropicKey("");
      setOpenaiKey("");
      setGeminiKey("");
      setSaved(true);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function clearKey(field: "anthropic_api_key" | "openai_api_key" | "gemini_api_key") {
    if (!confirm("Clear this API key?")) return;
    setSaving(true);
    setError(null);
    try {
      await apiFetch<LLMSettings>("/admin/llm", {
        method: "PUT",
        body: JSON.stringify({ provider, model, [field]: null }),
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to clear");
    } finally {
      setSaving(false);
    }
  }

  if (!settings) return <div>Loading…</div>;

  const selected = PROVIDERS.find((p) => p.value === provider);

  return (
    <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <label>
        <div style={lblStyle}>Provider</div>
        <select
          value={provider}
          onChange={(e) => setProvider(e.target.value as Provider)}
          style={inputStyle}
        >
          {PROVIDERS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        <div style={lblStyle}>Model</div>
        <input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder={selected?.modelHint ?? ""}
          required
          style={inputStyle}
        />
      </label>

      <KeyField
        label="Anthropic API key"
        value={anthropicKey}
        onChange={setAnthropicKey}
        isSet={settings.anthropic_api_key_set}
        hint={settings.anthropic_api_key_hint}
        placeholder="sk-ant-…"
        onClear={() => void clearKey("anthropic_api_key")}
        clearDisabled={saving || !settings.anthropic_api_key_set}
      />
      <KeyField
        label="OpenAI API key"
        value={openaiKey}
        onChange={setOpenaiKey}
        isSet={settings.openai_api_key_set}
        hint={settings.openai_api_key_hint}
        placeholder="sk-…"
        onClear={() => void clearKey("openai_api_key")}
        clearDisabled={saving || !settings.openai_api_key_set}
      />
      <KeyField
        label="Gemini API key"
        value={geminiKey}
        onChange={setGeminiKey}
        isSet={settings.gemini_api_key_set}
        hint={settings.gemini_api_key_hint}
        placeholder="AIza…"
        onClear={() => void clearKey("gemini_api_key")}
        clearDisabled={saving || !settings.gemini_api_key_set}
      />
      <label>
        <div style={lblStyle}>Ollama base URL</div>
        <input
          value={ollamaBaseUrl}
          onChange={(e) => setOllamaBaseUrl(e.target.value)}
          placeholder="http://localhost:11434 (leave blank for default)"
          style={inputStyle}
        />
      </label>

      {error && <div style={{ color: color.state.danger.fg }}>{error}</div>}
      {saved && <div style={{ color: color.state.success.fg }}>Saved.</div>}
      <div>
        <Button type="submit" variant="primary" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}

function KeyField({
  label,
  value,
  onChange,
  isSet,
  hint,
  placeholder,
  onClear,
  clearDisabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  isSet: boolean;
  hint: string;
  placeholder: string;
  onClear: () => void;
  clearDisabled: boolean;
}) {
  return (
    <label>
      <div style={{ ...lblStyle, display: "flex", alignItems: "center", gap: 6 }}>
        <span>{label}</span>
        {isSet && <span style={hintStyle}>currently {hint}</span>}
        <span style={{ flex: 1 }} />
        {isSet && (
          <Button
            type="button"
            size="sm"
            variant="danger"
            onClick={onClear}
            disabled={clearDisabled}
            style={{ padding: "2px 8px", fontSize: 12 }}
          >
            Clear
          </Button>
        )}
      </div>
      <input
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={isSet ? "leave blank to keep" : placeholder}
        style={inputStyle}
      />
    </label>
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

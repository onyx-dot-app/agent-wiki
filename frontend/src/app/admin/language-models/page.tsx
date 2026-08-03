"use client";

import {
  useEffect,
  useState,
  type Dispatch,
  type FormEvent,
  type InputHTMLAttributes,
  type SetStateAction,
} from "react";
import { mutate as globalMutate } from "swr";

import { Button } from "@onyx-ai/opal/components";
import { SvgCheckSmall } from "@onyx-ai/opal/icons";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
import {
  ALL_PROVIDERS,
  isConfigured,
  providerLabel,
  type LLMSettings,
  type Provider,
} from "@/lib/llm";
import { useIsMobile } from "@/lib/viewport";

interface ProviderMeta {
  defaultModel: string;
  keyLabel: string;
  keyPlaceholder: string;
  initial: string;
}

const PROVIDER_META: Record<Provider, ProviderMeta> = {
  anthropic: {
    defaultModel: "claude-sonnet-4-6",
    keyLabel: "API key",
    keyPlaceholder: "sk-ant-…",
    initial: "A",
  },
  openai: {
    defaultModel: "gpt-5.5",
    keyLabel: "API key",
    keyPlaceholder: "sk-…",
    initial: "O",
  },
  gemini: {
    defaultModel: "gemini-3.1-pro-preview",
    keyLabel: "API key",
    keyPlaceholder: "AIza…",
    initial: "G",
  },
  ollama: {
    defaultModel: "llama3.1",
    keyLabel: "Base URL",
    keyPlaceholder: "http://localhost:11434",
    initial: "L",
  },
  custom: {
    defaultModel: "",
    keyLabel: "API key",
    keyPlaceholder: "sk-…",
    initial: "C",
  },
  bedrock: {
    defaultModel: "us.anthropic.claude-sonnet-4-6",
    keyLabel: "Access key ID",
    keyPlaceholder: "AKIA…",
    initial: "B",
  },
};

const PROVIDER_MODELS: Record<Provider, string[]> = {
  anthropic: [
    "claude-fable-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-haiku-4-5",
  ],
  openai: [
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
  ],
  gemini: ["gemini-3.1-pro-preview", "gemini-3-flash-preview"],
  ollama: ["llama3.1", "llama3.2", "mistral", "phi3", "qwen2.5", "deepseek-r1"],
  custom: [],
  bedrock: [
    "us.anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.anthropic.claude-opus-4-8",
  ],
};

function keyHint(p: Provider, s: LLMSettings): string {
  if (p === "anthropic") return s.anthropic_api_key_hint;
  if (p === "openai") return s.openai_api_key_hint;
  if (p === "gemini") return s.gemini_api_key_hint;
  if (p === "ollama") return s.ollama_base_url || "http://localhost:11434";
  if (p === "custom") return s.custom_base_url;
  if (p === "bedrock") return s.bedrock_aws_region;
  return "";
}

export default function AdminLLMPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main className={`max-w-[760px] ${isMobile ? "px-3 py-4" : "px-8 py-6"}`}>
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
  const [expandedProvider, setExpandedProvider] = useState<Provider | null>(
    null,
  );

  async function load() {
    try {
      const r = await apiFetch<LLMSettings>("/admin/llm");
      setSettings(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  if (error)
    return <div className="text-(--status-text-error-05)">{error}</div>;
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
          <div className="text-sm text-(--text-03)">
            No providers configured yet.
          </div>
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
              onSaved={() => {
                void load();
                setExpandedProvider(null);
              }}
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
            <div className="mb-3 text-[13px] text-(--text-03)">
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
                  onSaved={() => {
                    void load();
                    setExpandedProvider(null);
                  }}
                />
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function AgentModelSection({
  settings,
  onSaved,
}: {
  settings: LLMSettings;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [selProvider, setSelProvider] = useState<Provider | "">(
    settings.provider as Provider | "",
  );
  const [selModel, setSelModel] = useState(settings.model);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const availableProviders = ALL_PROVIDERS.filter((p) =>
    isConfigured(p, settings),
  );

  // Flat list of every configured-provider + enabled-model combination.
  const options = availableProviders.flatMap((p) => {
    const models = settings.provider_models[p]?.length
      ? settings.provider_models[p]
      : (PROVIDER_MODELS[p] ?? []);
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
        <div className="flex items-center justify-between rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-01) px-4 py-3">
          <div>
            {settings.provider &&
            PROVIDER_META[settings.provider as Provider] ? (
              <>
                <span className="text-sm font-medium">
                  {providerLabel(settings.provider as Provider, settings)}
                </span>
                <span className="ml-2 text-sm text-(--text-03)">
                  {settings.model || "—"}
                </span>
              </>
            ) : (
              <span className="text-sm text-(--text-03)">
                No model selected — configure a provider below.
              </span>
            )}
          </div>
          <Button
            size="sm"
            variant="default"
            onClick={() => setEditing(true)}
            disabled={availableProviders.length === 0}
          >
            Edit
          </Button>
        </div>
      ) : (
        <div className="flex flex-col rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-01)">
          <div className="flex flex-col gap-1 p-3">
            {options.map(({ provider: p, model: m }) => {
              const isSelected = selProvider === p && selModel === m;
              return (
                <button
                  key={`${p}:${m}`}
                  type="button"
                  onClick={() => {
                    setSelProvider(p);
                    setSelModel(m);
                  }}
                  className={`flex cursor-pointer items-center gap-3 rounded-(--radius-04) border px-3 py-[10px] text-left ${isSelected ? "border-(--border-01) bg-(--background-tint-03)" : "border-(--border-01) bg-(--background-tint-00)"}`}
                >
                  <div
                    className={`flex h-[16px] w-[16px] shrink-0 items-center justify-center rounded-full ${isSelected ? "border-none bg-(--background-tint-inverted-00)" : "border-[1.5px] border-(--border-02) bg-transparent"}`}
                  >
                    {isSelected && (
                      <div className="h-[8px] w-[8px] rounded-full bg-(--text-inverted-05)" />
                    )}
                  </div>
                  <span
                    className={`shrink-0 text-[13px] font-medium ${isSelected ? "text-(--text-05)" : "text-(--text-04)"}`}
                  >
                    {providerLabel(p, settings)}
                  </span>
                  <span
                    className={`font-mono text-[13px] ${isSelected ? "text-(--text-05)" : "text-(--text-03)"}`}
                  >
                    {m}
                  </span>
                </button>
              );
            })}
          </div>
          {error && (
            <div className="px-3 pb-2 text-[13px] text-(--status-text-error-05)">
              {error}
            </div>
          )}
          <div className="flex gap-2 px-3 pt-1 pb-3">
            <Button
              type="button"
              variant="action"
              size="sm"
              disabled={saving || !selProvider}
              onClick={() => void onSave()}
            >
              {saving ? "Saving…" : "Set as active"}
            </Button>
            <Button
              type="button"
              variant="default"
              size="sm"
              onClick={() => {
                setEditing(false);
                setError(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

function ProviderCard({
  provider,
  settings,
  isActive,
  expanded,
  onToggle,
  onSaved,
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
    <div className="overflow-hidden rounded-(--radius-08) border border-(--border-01)">
      {/* Card header row */}
      <div className="flex items-center gap-3 bg-(--background-tint-01) px-4 py-3">
        {/* Provider initial icon */}
        <div className="flex h-[32px] w-[32px] shrink-0 items-center justify-center rounded-(--radius-04) bg-(--background-tint-02) text-[13px] font-bold text-(--text-04)">
          {meta.initial}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">
              {providerLabel(provider, settings)}
            </span>
            {isActive && (
              <span className="rounded-full border border-(--border-01) bg-(--background-tint-03) px-[6px] py-[2px] text-[11px] font-semibold text-(--text-05)">
                Agent
              </span>
            )}
          </div>
          {configured && hint && (
            <div className="mt-[2px] font-mono text-xs text-(--text-03)">
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
          {configured
            ? expanded
              ? "Close"
              : "Edit"
            : expanded
              ? "Close"
              : "Connect"}
        </Button>
      </div>

      {/* Expanded form */}
      {expanded && (
        <div className="border-t border-(--border-01) bg-(--background-tint-00) p-4">
          {provider === "custom" ? (
            <CustomProviderForm
              settings={settings}
              configured={configured}
              onSaved={onSaved}
            />
          ) : provider === "bedrock" ? (
            <BedrockProviderForm
              settings={settings}
              configured={configured}
              onSaved={onSaved}
            />
          ) : (
            <ProviderForm
              provider={provider}
              settings={settings}
              configured={configured}
              onSaved={onSaved}
            />
          )}
        </div>
      )}
    </div>
  );
}

function ProviderForm({
  provider,
  settings,
  configured,
  onSaved,
}: {
  // Custom + bedrock route to their own multi-field forms — encode that here.
  provider: Exclude<Provider, "custom" | "bedrock">;
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
  const confirmDialog = useConfirm();

  const keyField = `${provider}_api_key` as
    | "anthropic_api_key"
    | "openai_api_key"
    | "gemini_api_key";
  const currentHint = keyHint(provider, settings);

  function toggleModel(id: string) {
    setSelectedModels((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
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
      const updated = {
        ...settings.provider_models,
        [provider]: enabledModels,
      };
      body.provider_models = updated;
      await apiFetch("/admin/llm", {
        method: "PUT",
        body: JSON.stringify(body),
      });
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
    if (
      !(await confirmDialog({
        title: `Remove ${providerLabel(provider, settings)} credentials?`,
        confirmLabel: "Remove",
      }))
    )
      return;
    setSaving(true);
    setError(null);
    try {
      const body: Record<string, unknown> = isOllama
        ? { ollama_base_url: null }
        : { [keyField]: null };
      await apiFetch("/admin/llm", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to remove");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <Field
        label={meta.keyLabel}
        type={isOllama ? "text" : "password"}
        value={keyValue}
        onChange={(e) => setKeyValue(e.target.value)}
        placeholder={
          configured
            ? isOllama
              ? currentHint
              : "leave blank to keep current"
            : meta.keyPlaceholder
        }
      />

      <div>
        <div className="mb-2 flex items-baseline justify-between">
          <div className={lblClass}>Models</div>
          <div className="text-xs text-(--text-03)">
            Select models to make available
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          {knownModels.map((id) => {
            const checked = selectedModels.has(id);
            return (
              <button
                key={id}
                type="button"
                onClick={() => toggleModel(id)}
                className={`flex cursor-pointer items-center gap-[10px] rounded-(--radius-08) border px-3 py-[10px] text-left ${checked ? "border-(--border-01) bg-(--background-tint-03)" : "border-(--border-01) bg-(--background-tint-00)"}`}
              >
                <div
                  className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-(--radius-04) ${checked ? "border-none bg-(--background-tint-inverted-00)" : "border-[1.5px] border-(--border-02) bg-transparent"}`}
                >
                  {checked && (
                    <span className="flex text-(--text-inverted-05)">
                      <SvgCheckSmall size={11} />
                    </span>
                  )}
                </div>
                <span
                  className={`font-mono text-[13px] ${checked ? "text-(--text-05)" : "text-(--text-05)"}`}
                >
                  {id}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <TestConnection
        provider={provider}
        model={knownModels.find((m) => selectedModels.has(m)) ?? null}
        configured={configured}
      />

      <FormMessages error={error} saved={saved} />
      <FormActions saving={saving} configured={configured} onClear={onClear} />
    </form>
  );
}

interface ProviderTestResult {
  ok: boolean;
  base_url: string;
  auth_present: boolean;
  model: string;
  models_endpoint: string;
  completion: string;
}

function TestConnection({
  provider,
  model,
  configured,
}: {
  provider: Provider;
  model: string | null;
  configured: boolean;
}) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ProviderTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onTest() {
    setTesting(true);
    setResult(null);
    setError(null);
    try {
      const r = await apiFetch<ProviderTestResult>(
        `/admin/llm/${provider}/test`,
        {
          method: "POST",
          body: JSON.stringify({ model }),
        },
      );
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "test failed");
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="default"
          size="sm"
          disabled={testing || !configured}
          onClick={() => void onTest()}
        >
          {testing ? "Testing…" : "Test connection"}
        </Button>
        <span className="text-xs text-(--text-03)">
          Tests the saved configuration.
        </span>
      </div>
      {error && (
        <div className="text-[13px] text-(--status-text-error-05)">{error}</div>
      )}
      {result && (
        <div className="flex flex-col gap-1 font-mono text-xs">
          <div
            className={
              result.ok
                ? "text-(--status-text-success-05)"
                : "text-(--status-text-error-05)"
            }
          >
            {result.ok
              ? `✓ ${result.model} responded`
              : `✗ ${result.completion}`}
          </div>
          <div className="text-(--text-03)">
            {result.models_endpoint === "ok"
              ? "✓ models endpoint reachable"
              : `• models endpoint: ${result.models_endpoint}`}
          </div>
          <div className="text-(--text-03)">
            {result.auth_present ? "auth: key sent" : "auth: keyless"}
            {result.base_url ? ` · ${result.base_url}` : ""}
          </div>
        </div>
      )}
    </div>
  );
}

// Free-add list of model IDs, shared by the providers without a fixed catalog
// (custom OpenAI-compatible gateways and Bedrock).
function ModelListEditor({
  models,
  setModels,
  placeholder,
  hint,
}: {
  models: string[];
  setModels: Dispatch<SetStateAction<string[]>>;
  placeholder: string;
  hint: string;
}) {
  const [newModel, setNewModel] = useState("");

  function addModel() {
    const m = newModel.trim();
    if (!m || models.includes(m)) return;
    setModels((prev) => [...prev, m]);
    setNewModel("");
  }

  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between">
        <div className={lblClass}>Models</div>
        <div className="text-xs text-(--text-03)">{hint}</div>
      </div>
      <div className="flex flex-col gap-1.5">
        {models.map((id) => (
          <div
            key={id}
            className="flex items-center justify-between rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-00) py-[6px] pr-2 pl-3"
          >
            <span className="font-mono text-[13px] text-(--text-05)">{id}</span>
            <Button
              type="button"
              variant="default"
              size="sm"
              onClick={() => setModels((prev) => prev.filter((m) => m !== id))}
            >
              Remove
            </Button>
          </div>
        ))}
        <div className="flex gap-2">
          <input
            type="text"
            value={newModel}
            onChange={(e) => setNewModel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addModel();
              }
            }}
            placeholder={placeholder}
            className={inputClass}
          />
          <Button type="button" variant="default" size="sm" onClick={addModel}>
            Add
          </Button>
        </div>
      </div>
    </div>
  );
}

function CustomProviderForm({
  settings,
  configured,
  onSaved,
}: {
  settings: LLMSettings;
  configured: boolean;
  onSaved: () => void;
}) {
  const [baseUrl, setBaseUrl] = useState(settings.custom_base_url);
  const [keyValue, setKeyValue] = useState("");
  const [displayName, setDisplayName] = useState(settings.custom_display_name);
  const [models, setModels] = useState<string[]>(
    () => settings.provider_models["custom"] ?? [],
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const confirmDialog = useConfirm();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const body: Record<string, unknown> = {
        custom_display_name: displayName.trim(),
        provider_models: { ...settings.provider_models, custom: models },
      };
      // Empty string means "keep current" server-side; only send a real URL.
      if (baseUrl.trim()) body.custom_base_url = baseUrl.trim();
      if (keyValue) body.custom_api_key = keyValue;
      await apiFetch("/admin/llm", {
        method: "PUT",
        body: JSON.stringify(body),
      });
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
    if (
      !(await confirmDialog({
        title: "Remove custom provider credentials?",
        confirmLabel: "Remove",
      }))
    )
      return;
    setSaving(true);
    setError(null);
    try {
      await apiFetch("/admin/llm", {
        method: "PUT",
        body: JSON.stringify({
          custom_base_url: null,
          custom_api_key: null,
          custom_display_name: "",
          provider_models: { ...settings.provider_models, custom: [] },
        }),
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to remove");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <Field
        label="Base URL"
        type="text"
        value={baseUrl}
        onChange={(e) => setBaseUrl(e.target.value)}
        placeholder="https://openrouter.ai/api/v1"
        hint="Any OpenAI-compatible endpoint. Requests go to /chat/completions under this URL."
      />

      <Field
        label="API key (optional)"
        type="password"
        value={keyValue}
        onChange={(e) => setKeyValue(e.target.value)}
        placeholder={
          settings.custom_api_key_set ? "leave blank to keep current" : "sk-…"
        }
      />

      <Field
        label="Display name (optional)"
        type="text"
        value={displayName}
        onChange={(e) => setDisplayName(e.target.value)}
        placeholder="Custom"
      />

      <ModelListEditor
        models={models}
        setModels={setModels}
        placeholder="deepseek-chat"
        hint="Add at least one model name"
      />

      <TestConnection
        provider="custom"
        model={models[0] ?? null}
        configured={configured}
      />

      <FormMessages error={error} saved={saved} />
      <FormActions saving={saving} configured={configured} onClear={onClear} />
    </form>
  );
}

function BedrockProviderForm({
  settings,
  configured,
  onSaved,
}: {
  settings: LLMSettings;
  configured: boolean;
  onSaved: () => void;
}) {
  const [region, setRegion] = useState(settings.bedrock_aws_region);
  const [endpoint, setEndpoint] = useState(settings.bedrock_endpoint_url);
  const [accessKeyId, setAccessKeyId] = useState("");
  const [secretAccessKey, setSecretAccessKey] = useState("");
  const [sessionToken, setSessionToken] = useState("");
  const [bearerToken, setBearerToken] = useState("");
  const [models, setModels] = useState<string[]>(
    () => settings.provider_models["bedrock"] ?? PROVIDER_MODELS.bedrock,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const confirmDialog = useConfirm();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!region.trim() && !configured) {
      setError("AWS region is required (e.g. us-gov-west-1).");
      return;
    }
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const body: Record<string, unknown> = {
        provider_models: { ...settings.provider_models, bedrock: models },
      };
      // Blank means "keep current" server-side; only send real values.
      if (region.trim()) body.bedrock_aws_region = region.trim();
      if (endpoint.trim()) body.bedrock_endpoint_url = endpoint.trim();
      if (accessKeyId) body.bedrock_aws_access_key_id = accessKeyId;
      if (secretAccessKey) body.bedrock_aws_secret_access_key = secretAccessKey;
      if (sessionToken) body.bedrock_aws_session_token = sessionToken;
      if (bearerToken) body.bedrock_aws_bearer_token = bearerToken;
      await apiFetch("/admin/llm", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      setAccessKeyId("");
      setSecretAccessKey("");
      setSessionToken("");
      setBearerToken("");
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
    if (
      !(await confirmDialog({
        title: "Remove Amazon Bedrock credentials?",
        confirmLabel: "Remove",
      }))
    )
      return;
    setSaving(true);
    setError(null);
    try {
      await apiFetch("/admin/llm", {
        method: "PUT",
        body: JSON.stringify({
          bedrock_aws_region: null,
          bedrock_endpoint_url: null,
          bedrock_aws_access_key_id: null,
          bedrock_aws_secret_access_key: null,
          bedrock_aws_session_token: null,
          bedrock_aws_bearer_token: null,
          provider_models: { ...settings.provider_models, bedrock: [] },
        }),
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to remove");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <Field
        label="AWS region"
        type="text"
        value={region}
        onChange={(e) => setRegion(e.target.value)}
        placeholder="us-gov-west-1"
        hint="GovCloud regions (us-gov-west-1 / us-gov-east-1) route to the GovCloud partition automatically."
      />

      <Field
        label="Bedrock API key (optional)"
        type="password"
        value={bearerToken}
        onChange={(e) => setBearerToken(e.target.value)}
        placeholder={
          settings.bedrock_aws_bearer_token_set
            ? "leave blank to keep current"
            : "a Bedrock API key (alternative to AWS access keys)"
        }
        hint="Use this OR the AWS access key + secret below — not both."
      />

      <Field
        label="Access key ID (optional)"
        type="password"
        value={accessKeyId}
        onChange={(e) => setAccessKeyId(e.target.value)}
        placeholder={
          settings.bedrock_aws_access_key_id_set
            ? "leave blank to keep current"
            : "AKIA…  (blank = use the instance/IAM role)"
        }
      />

      <Field
        label="Secret access key (optional)"
        type="password"
        value={secretAccessKey}
        onChange={(e) => setSecretAccessKey(e.target.value)}
        placeholder={
          settings.bedrock_aws_secret_access_key_set
            ? "leave blank to keep current"
            : "…"
        }
      />

      <Field
        label="Session token (optional)"
        type="password"
        value={sessionToken}
        onChange={(e) => setSessionToken(e.target.value)}
        placeholder={
          settings.bedrock_aws_session_token_set
            ? "leave blank to keep current"
            : "for temporary credentials"
        }
      />

      <Field
        label="Endpoint URL (optional)"
        type="text"
        value={endpoint}
        onChange={(e) => setEndpoint(e.target.value)}
        placeholder="https://bedrock-runtime-fips.us-gov-west-1.amazonaws.com"
        hint="Only for a FIPS or private (PrivateLink) endpoint; blank derives it from the region."
      />

      <ModelListEditor
        models={models}
        setModels={setModels}
        placeholder="us.anthropic.claude-sonnet-4-6"
        hint="Bedrock model IDs enabled in your account"
      />

      <TestConnection
        provider="bedrock"
        model={models[0] ?? null}
        configured={configured}
      />

      <FormMessages error={error} saved={saved} />
      <FormActions saving={saving} configured={configured} onClear={onClear} />
    </form>
  );
}

function Field({
  label,
  hint,
  ...inputProps
}: { label: string; hint?: string } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label>
      <div className={lblClass}>{label}</div>
      <input className={inputClass} {...inputProps} />
      {hint && <div className="mt-1 text-xs text-(--text-03)">{hint}</div>}
    </label>
  );
}

function FormMessages({
  error,
  saved,
}: {
  error: string | null;
  saved: boolean;
}) {
  return (
    <>
      {error && (
        <div className="text-[13px] text-(--status-text-error-05)">{error}</div>
      )}
      {saved && (
        <div className="text-[13px] text-(--status-text-success-05)">
          Saved.
        </div>
      )}
    </>
  );
}

function FormActions({
  saving,
  configured,
  onClear,
}: {
  saving: boolean;
  configured: boolean;
  onClear: () => void;
}) {
  return (
    <div className="flex gap-2">
      <Button type="submit" variant="action" size="sm" disabled={saving}>
        {saving ? "Saving…" : "Save"}
      </Button>
      {configured && (
        <Button
          type="button"
          variant="danger"
          size="sm"
          disabled={saving}
          onClick={onClear}
        >
          Remove
        </Button>
      )}
    </div>
  );
}

const inputClass =
  "w-full py-2 px-[10px] box-border border border-(--border-01) rounded-(--radius-04) text-sm";
const lblClass = "mb-1 text-[13px] font-medium";
const sectionHeaderClass =
  "text-[13px] font-semibold text-(--text-04) uppercase tracking-[0.05em] mb-3";
